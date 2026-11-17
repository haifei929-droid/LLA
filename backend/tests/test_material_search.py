"""Material discovery: transcription alignment (Spec 24.1), the legacy
difficulty recommender values, and the governed next-material pipeline.""" 

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.speech import RecognizedSegment
from app.adapters.web_material import BBCLearningEnglishProvider, align_sentences, split_sentences
from app.core.material_candidates import SOURCE_LABELS
from app.core.material_recommender import MaterialRecommender
from app.core.material_search import MaterialSearchService
from tests.fixtures import create_material, make_database, make_settings, make_sine_wav


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


def test_align_sentences_clamps_small_anchor_overlap() -> None:
    segments = [
        RecognizedSegment(text="First sentence.", start_time=0.0, end_time=2.0),
        RecognizedSegment(text="Second sentence.", start_time=1.8, end_time=4.0),
    ]
    aligned = align_sentences(
        ["First sentence.", "Second sentence.", "A final sentence."],
        segments,
    )

    assert all(
        current[1] >= previous[2]
        for previous, current in zip(aligned, aligned[1:])
    )
    assert all(sentence[2] > sentence[1] for sentence in aligned)


def test_align_sentences_normalizes_minimum_interpolation_span() -> None:
    segments = [
        RecognizedSegment(text="First sentence.", start_time=0.0, end_time=2.0),
        RecognizedSegment(text="Third sentence.", start_time=2.000000000000001, end_time=4.0),
    ]
    aligned = align_sentences(
        ["First sentence.", "Second sentence.", "Third sentence."],
        segments,
    )

    assert all(
        current[1] >= previous[2]
        for previous, current in zip(aligned, aligned[1:])
    )
    assert all(sentence[2] > sentence[1] for sentence in aligned)


def test_bbc_provider_skips_episode_without_downloadable_audio(monkeypatch, tmp_path: Path) -> None:
    provider = BBCLearningEnglishProvider()
    bad_url = "https://www.bbc.co.uk/learningenglish/english/features/6-minute-english/ep-170622"
    good_url = "https://www.bbc.co.uk/learningenglish/english/features/6-minute-english/ep-170623"
    visited: list[str] = []

    monkeypatch.setattr(provider, "_list_episode_urls", lambda: [bad_url, good_url])

    def fetch_episode(url: str) -> tuple[str, str, str]:
        visited.append(url)
        if url == bad_url:
            raise ValueError(f"Episode has no downloadable transcript/audio: {url}")
        return "Usable episode", "One sentence. Two sentence. Three sentence.", "https://example.test/good.mp3"

    def download(_url: str, path: Path) -> None:
        path.write_bytes(b"mp3")

    def transcode(args: list[str], check: bool) -> None:
        Path(args[-1]).write_bytes(b"wav")

    monkeypatch.setattr(provider, "_fetch_episode", fetch_episode)
    monkeypatch.setattr("app.adapters.web_material.urllib.request.urlretrieve", download)
    monkeypatch.setattr("app.adapters.web_material.subprocess.run", transcode)
    monkeypatch.setattr("app.adapters.web_material._wav_duration", lambda _path: 60.0)

    source = provider.search_next(exclude_urls=set(), work_dir=tmp_path, asr=None)

    assert source.material_id == "web-6me-170623"
    assert source.source_url == good_url
    assert visited == [bad_url, good_url]


class FakeASR:
    """Sentence-shaped deterministic segments covering the fixture audio."""

    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        return [
            RecognizedSegment(
                text=f"Sample sentence number {index} has enough words.",
                start_time=index * 1.9,
                end_time=index * 1.9 + 1.8,
            )
            for index in range(32)
        ]


OFFICIAL_TRANSCRIPT = " ".join(
    f"Official sentence number {index} of this lesson." for index in range(1, 13)
)
PLATFORM = "50LANGUAGES"


def catalog_item(
    index: int,
    *,
    item_order: int | None = None,
    accent: str = "en-GB",
    cefr_level: str = "A1",
    poor_audio: bool = False,
) -> dict[str, object]:
    return {
        "source_item_id": f"web-test-{index:03d}",
        "title": f"Fake episode {index}",
        "source_url": f"https://example.com/ep-{index}",
        "audio_url": (
            f"https://example.com/ep-{index}-poor.mp3" if poor_audio
            else f"https://example.com/ep-{index}.mp3"
        ),
        "duration_seconds": 65.0,
        "source_course": "fixture-course",
        "course_order": 1,
        "item_order": item_order if item_order is not None else index,
        "cefr_level": cefr_level,
        "accent": accent,
        "speaker_id": "speaker-a",
        "transcript": OFFICIAL_TRANSCRIPT,
        "transcript_origin": "OFFICIAL",
        "transcript_verified": True,
        "license_status": "CLEARED",
        "license_reference": "fixture",
    }


class GovernedFakeProvider:
    """Offline provider exposing a declared catalog snapshot."""

    def __init__(self, tmp_path: Path, items: list[dict[str, object]] | None = None) -> None:
        self.tmp_path = tmp_path
        self.items = items if items is not None else [catalog_item(1), catalog_item(2)]
        self.calls = 0

    def catalog_items(self) -> list[dict[str, object]]:
        self.calls += 1
        return list(self.items)

    def catalog_info(self) -> dict[str, object]:
        return {
            "catalog_complete": True,
            "known_total": len(self.items),
            "has_cursor": False,
            "catalog_scope": "fixture",
        }

    def download_audio(self, mp3_url: str, audio_id: str, work_dir: Path) -> Path:
        wav_path = work_dir / f"{audio_id}.wav"
        if "poor" in mp3_url:
            make_sine_wav(wav_path, segments=[(5.0, 12000.0)])  # too short -> quality FAIL
        else:
            make_sine_wav(wav_path, segments=[(65.0, 12000.0)])
        return wav_path


def _governed_service(tmp_path: Path, provider: GovernedFakeProvider) -> MaterialSearchService:
    service = MaterialSearchService(
        database=make_database(tmp_path),
        settings=make_settings(tmp_path),
        providers=[provider],
        asr=FakeASR(),
    )
    service.candidates.activate_source(
        "default", PLATFORM, "fixture-license", "fixture-catalog", confirmed_by="TEST"
    )
    return service


def test_search_next_requires_an_active_source(tmp_path: Path) -> None:
    service = MaterialSearchService(
        database=make_database(tmp_path),
        settings=make_settings(tmp_path),
        providers=[GovernedFakeProvider(tmp_path)],
        asr=FakeASR(),
    )
    with pytest.raises(ValueError, match="SOURCE_NOT_APPROVED"):
        service.search_next()


def test_search_service_pipeline_with_fake_provider(tmp_path: Path) -> None:
    provider = GovernedFakeProvider(tmp_path)
    service = _governed_service(tmp_path, provider)

    result = service.search_next()

    assert result["material_id"] == "web-test-001"
    assert result["source_url"] == "https://example.com/ep-1"
    assert result["source_platform"] == PLATFORM
    # The governed source label replaces the provider's own display name.
    assert result["source_name"] == SOURCE_LABELS[PLATFORM]
    assert result["ingestion_status"] == "INGESTED"
    assert result["accent"] == "en-GB"
    assert result["cefr_level"] == "A1"

    with service.database.connect() as connection:
        material = connection.execute(
            "SELECT source_url, source_name, duration_seconds, accent, transcript_verified "
            "FROM materials WHERE material_id = 'web-test-001'"
        ).fetchone()
        sentences = connection.execute(
            "SELECT text, start_time, end_time FROM sentences WHERE material_id = 'web-test-001' ORDER BY sequence_no"
        ).fetchall()
    assert material["source_url"] == "https://example.com/ep-1"
    assert material["source_name"] == SOURCE_LABELS[PLATFORM]
    assert material["accent"] == "en-GB"
    assert material["transcript_verified"] == 1
    assert [row["text"] for row in sentences] == [
        f"Official sentence number {index} of this lesson." for index in range(1, 13)
    ]
    assert sentences[0]["start_time"] >= 0.0
    assert all(
        current["start_time"] >= previous["end_time"]
        for previous, current in zip(sentences, sentences[1:])
    )

    # The next fetch advances to the next course item, never back to an
    # already ingested one.
    second = service.search_next()
    assert second["material_id"] == "web-test-002"


def test_search_next_never_uses_the_two_week_upgrade_rule(tmp_path: Path) -> None:
    provider = GovernedFakeProvider(tmp_path)
    service = _governed_service(tmp_path, provider)
    create_material(service.database, "done-001")
    with service.database.connect() as connection:
        connection.execute(
            "UPDATE training_progress SET current_state = 'FULLY_COMPLETED' WHERE material_id = 'done-001'"
        )
        connection.execute(
            "UPDATE materials SET duration_seconds = 900, speech_rate_wpm = 106 WHERE material_id = 'done-001'"
        )
        # Two consecutive weekly gate passes would have upgraded under the old
        # recommendation rule; the eight-week confirmation is the authority now.
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

    result = service.search_next()
    assert result["upgrade_available"] is False
    assert result["material_id"] == "web-test-001"


def test_quality_gate_rejects_poor_audio(tmp_path: Path) -> None:
    provider = GovernedFakeProvider(
        tmp_path,
        [catalog_item(1, item_order=1, poor_audio=True), catalog_item(2, item_order=2)],
    )
    service = _governed_service(tmp_path, provider)

    result = service.search_next()
    # The poor item is rejected outright; the next qualified item is ingested.
    assert result["material_id"] == "web-test-002"
    quarantined = {
        row["provider_item_id"]: row["ingestion_status"]
        for row in service.candidates.list_candidates("default")
    }
    assert quarantined == {"web-test-001": "REJECTED", "web-test-002": "INGESTED"}


def test_skip_retires_material(tmp_path: Path) -> None:
    provider = GovernedFakeProvider(tmp_path)
    service = _governed_service(tmp_path, provider)
    result = service.search_next()

    skipped = service.skip(result["material_id"])
    assert skipped["status"] == "SKIPPED"
    with service.database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM materials WHERE material_id = ?", (result["material_id"],)
        ).fetchone()["status"]
    assert status == "SKIPPED"
    with pytest.raises(KeyError):
        service.skip("no-such-material")

    # The next fetch advances to the next course item; skipping never re-opens
    # the retired one and never invents a new source.
    second = service.search_next()
    assert second["material_id"] == "web-test-002"
    with pytest.raises(ValueError, match="No qualified material"):
        service.search_next()

