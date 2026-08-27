"""Auto-search material: difficulty rules (Spec 3.1/16), transcript-ASR
alignment (Spec 24.1), and the search service pipeline with a fake provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.speech import RecognizedSegment
from app.adapters.web_material import align_sentences, split_sentences
from app.core.material_recommender import MaterialRecommender, SearchCriteria
from app.core.material_search import MaterialSearchService
from app.db.connection import Database
from tests.fixtures import create_material, make_database, make_settings


def test_recommender_starts_at_standard_slow() -> None:
    criteria = MaterialRecommender().next_criteria(None, recent_gate_passes=0)
    assert criteria.duration_band == "standard"
    assert criteria.wpm_max == 120.0
    assert criteria.upgrade_available is False


def test_recommender_upgrades_one_variable_after_stable_passes() -> None:
    recommender = MaterialRecommender()
    profile = {"duration_minutes": 15.0, "wpm": 106.0}  # standard + slow

    # Not stable yet: same criteria, no upgrade offered.
    same = recommender.next_criteria(profile, recent_gate_passes=1)
    assert same.upgrade_available is False
    assert same.duration_band == "standard"
    assert same.wpm_max == 120.0

    # Stable: duration upgrades first (standard -> long), rate unchanged.
    upgraded = recommender.next_criteria(profile, recent_gate_passes=2)
    assert upgraded.upgrade_available is True
    assert upgraded.duration_band == "long"
    assert upgraded.wpm_max == 120.0

    # Next stable round: rate upgrades (slow -> medium), duration stays long.
    profile_long = {"duration_minutes": 25.0, "wpm": 110.0}
    next_round = recommender.next_criteria(profile_long, recent_gate_passes=2)
    assert next_round.duration_band == "long"
    assert next_round.wpm_max == 165.0


def test_split_sentences() -> None:
    text = "Hello there. This is a second sentence! And a third? Done."
    assert split_sentences(text) == ["Hello there.", "This is a second sentence!", "And a third?", "Done."]


def test_align_sentences_maps_to_segment_timestamps() -> None:
    segments = [
        RecognizedSegment(text="Hello there.", start_time=0.0, end_time=2.0),
        RecognizedSegment(text="This is a second sentence.", start_time=2.5, end_time=6.0),
        RecognizedSegment(text="And a third one.", start_time=6.5, end_time=8.0),
    ]
    sentences = ["Hello there.", "This is a second sentence.", "And a third one."]
    aligned = align_sentences(sentences, segments)
    assert aligned[0][1] == pytest.approx(0.0, abs=0.1)
    assert aligned[1][1] == pytest.approx(2.5, abs=0.3)
    assert aligned[1][2] == pytest.approx(6.0, abs=0.3)
    assert aligned[2][1] == pytest.approx(6.5, abs=0.3)


class FakeProvider:
    def __init__(self, tmp_path: Path, pass_quality: bool = True) -> None:
        self.called_with: dict[str, object] = {}
        self._count = 0
        self._tmp_path = tmp_path
        self.pass_quality = pass_quality

    def search_next(self, *, exclude_urls, work_dir, asr=None, criteria=None) -> object:
        self.called_with = {
            "exclude_urls": exclude_urls,
            "work_dir": work_dir,
            "asr": asr,
            "criteria": criteria,
        }
        from app.adapters.web_material import MaterialSource
        from tests.fixtures import make_sine_wav

        self._count += 1
        wav_path = work_dir / f"web-test-{self._count:03d}.wav"
        if self.pass_quality:
            make_sine_wav(wav_path, segments=[(65.0, 12000.0)])  # >= min duration, no silence
        else:
            make_sine_wav(wav_path, segments=[(2.0, 12000.0)])  # too short -> quality FAIL
        return MaterialSource(
            material_id=f"web-test-{self._count:03d}",
            title="Fake episode",
            audio_path=str(wav_path),
            transcript="One sentence here. Two sentences there. Three more words.",
            source_url=f"https://example.com/ep-{self._count}",
            source_name="Fake Source",
            duration_seconds=65.0 if self.pass_quality else 2.0,
            timestamped_sentences=(
                ("One sentence here.", 0.0, 5.0),
                ("Two sentences there.", 5.5, 12.0),
                ("Three more words.", 12.5, 18.0),
            ),
        )


def test_search_service_pipeline_with_fake_provider(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    provider = FakeProvider(tmp_path)
    service = MaterialSearchService(database, settings, providers=[provider], asr=None)

    result = service.search_next()

    assert result["material_id"] == "web-test-001"
    assert result["source_url"] == "https://example.com/ep-1"
    assert result["upgrade_available"] is False
    # Criteria: initial profile (no completed material) -> standard + slow.
    assert provider.called_with["criteria"].duration_band == "standard"

    with database.connect() as connection:
        material = connection.execute(
            "SELECT source_url, source_name, duration_seconds FROM materials WHERE material_id = 'web-test-001'"
        ).fetchone()
        sentences = connection.execute(
            "SELECT text, start_time, end_time FROM sentences WHERE material_id = 'web-test-001' ORDER BY sequence_no"
        ).fetchall()
    assert material["source_url"] == "https://example.com/ep-1"
    assert material["source_name"] == "Fake Source"
    assert [row["text"] for row in sentences] == [
        "One sentence here.", "Two sentences there.", "Three more words."
    ]
    assert sentences[0]["start_time"] == 0.0
    assert sentences[2]["end_time"] == 18.0

    # Second search excludes the imported URL and uses a fresh episode.
    second = service.search_next()
    assert "https://example.com/ep-1" in provider.called_with["exclude_urls"]
    assert second["material_id"] == "web-test-002"


def test_search_service_uses_gate_stability_for_upgrade(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    # One completed material with a standard-duration + slow-rate profile.
    create_material(database, "done-001")
    with database.connect() as connection:
        connection.execute(
            "UPDATE training_progress SET current_state = 'FULLY_COMPLETED' WHERE material_id = 'done-001'"
        )
        connection.execute(
            "UPDATE materials SET duration_seconds = 900, speech_rate_wpm = 106 WHERE material_id = 'done-001'"
        )
        # Two consecutive weekly gate passes.
        for week in ("W1", "W2"):
            connection.execute(
                """
                INSERT INTO weekly_assessments(
                    week_id, period_start, period_end, dictation_required, reading_required,
                    gate_status, created_at
                ) VALUES (?, '2026-08-24', '2026-08-30', 1, 0, 'WEEKLY_GATE_PASS', datetime('now'))
                """,
                (week,),
            )

    provider = FakeProvider(tmp_path)
    service = MaterialSearchService(database, settings, providers=[provider], asr=None)
    result = service.search_next()
    assert result["upgrade_available"] is True
    assert provider.called_with["criteria"].duration_band == "long"


def test_quality_gate_rejects_poor_audio_and_falls_through(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    poor = FakeProvider(tmp_path, pass_quality=False)
    good = FakeProvider(tmp_path, pass_quality=True)
    service = MaterialSearchService(database, settings, providers=[poor, good], asr=None)

    result = service.search_next()
    # The first provider's audio fails the quality gate; the second wins.
    assert result["material_id"] == "web-test-001"  # good provider's first episode
    assert result["source_url"] == "https://example.com/ep-1"


def test_skip_retires_material(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    provider = FakeProvider(tmp_path)
    service = MaterialSearchService(database, settings, providers=[provider], asr=None)
    result = service.search_next()

    skipped = service.skip(result["material_id"])
    assert skipped["status"] == "SKIPPED"
    with database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM materials WHERE material_id = ?", (result["material_id"],)
        ).fetchone()["status"]
    assert status == "SKIPPED"
    with pytest.raises(KeyError):
        service.skip("no-such-material")
