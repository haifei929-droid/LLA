"""M5: exception handling and recoverability (Spec 31.10).

- A failing analysis must never mark PASS; the recording stays on disk and
  can be re-analyzed.
- An unfinished time log never counts toward learning stats.
- A failed material search (network/ASR) degrades to the next provider and
  never disturbs existing training state.
- Material search failures surface as 409, not 500.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.core.audio_quality import AudioQualityAnalyzer
from app.core.material_search import MaterialSearchService
from app.core.reading_service import ReadingService
from app.db.connection import Database
from tests.fixtures import DEFAULT_SENTENCES, make_database, make_settings, make_sine_wav

REFERENCE = [(2.0, 12000.0), (0.5, 0.0)]


def _material_with_audio(tmp_path: Path, database: Database, material_id: str = "m1") -> str:
    audio = tmp_path / "data" / "materials" / f"{material_id}.wav"
    make_sine_wav(audio, segments=REFERENCE)
    from tests.fixtures import create_material

    create_material(database, material_id)
    with database.connect() as connection:
        connection.execute(
            "UPDATE materials SET audio_path = ? WHERE material_id = ?", (str(audio), material_id)
        )
    return material_id


class FailingAnalyzer:
    """A deterministic analyzer that always fails mid-analysis."""

    def analyze(self, audio_path: str):
        raise RuntimeError("simulated analysis failure")


def test_failed_analysis_never_marks_pass_and_keeps_recording(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    material_id = _material_with_audio(tmp_path, database)
    settings = make_settings(tmp_path)
    # Drive to READING_AVAILABLE so scoring is allowed.
    from app.core.dictation_service import DictationService
    from app.core.training_events import TrainingEventService

    events = TrainingEventService(database)
    events.complete_first_listen(material_id)
    events.submit_comprehension(material_id=material_id, phase="FIRST", self_rating="30\u201350%", summary="x.")
    dictation = DictationService(database)
    for part in (1, 2, 3):
        for index in range((part - 1) * 3, part * 3):
            dictation.submit(
                material_id=material_id,
                sentence_id=f"{material_id}-sentence-{index + 1:03d}",
                user_text=DEFAULT_SENTENCES[index],
                listen_count=1,
            )
    # The final sentence of each Part completes it atomically inside submit.
    events.complete_second_listen(material_id)
    events.submit_comprehension(material_id=material_id, phase="SECOND", self_rating=">70%", summary="x.")

    recording = tmp_path / "recording.wav"
    make_sine_wav(recording, segments=[(2.0, 12000.0), (0.5, 0.0)])
    service = ReadingService(database, settings, analyzer=FailingAnalyzer())

    with pytest.raises(RuntimeError, match="simulated"):
        service.score(material_id=material_id, scope="PART", part_no=1, recording_path=recording)

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM reading_attempts WHERE material_id = ?", (material_id,)
        ).fetchone()["n"]
    assert count == 0, "failed analysis must not write a score row"
    assert recording.is_file(), "the recording must be kept for re-analysis"


def test_unfinished_time_log_never_counts(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    from app.core.learning_time import LearningTimeService

    service = LearningTimeService(database)
    started = service.start(activity_type="DICTATION")
    # Never stop: the interval must not appear in stats.
    assert service.stats()["total_learning_seconds"] == 0
    service.stop(started["time_log_id"], 120)
    assert service.stats()["total_learning_seconds"] == 120


class BoomProvider:
    def search_next(self, *, exclude_urls, work_dir, asr=None, criteria=None):
        raise RuntimeError("network down")


class GoodProvider:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def search_next(self, *, exclude_urls, work_dir, asr=None, criteria=None):
        from app.adapters.web_material import MaterialSource
        from tests.fixtures import make_sine_wav

        wav = self.tmp_path / "good.wav"
        make_sine_wav(wav, segments=[(65.0, 12000.0)])
        return MaterialSource(
            material_id="good-001",
            title="Good source",
            audio_path=str(wav),
            transcript="One sentence here. Two sentences there. Three more words.",
            source_url="https://example.com/good",
            source_name="Good",
            duration_seconds=65.0,
            timestamped_sentences=(
                ("One sentence here.", 0.0, 5.0),
                ("Two sentences there.", 5.5, 12.0),
                ("Three more words.", 12.5, 18.0),
            ),
        )


def test_search_failure_degrades_to_next_provider_and_keeps_state(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    create_before = MaterialSearchService(database, settings, providers=[], asr=None)
    from tests.fixtures import create_material

    create_material(database, "keep-001")
    with database.connect() as connection:
        connection.execute(
            "UPDATE training_progress SET current_state = 'DICTATION_PART_2' WHERE material_id = 'keep-001'"
        )

    service = MaterialSearchService(
        database, settings, providers=[BoomProvider(), GoodProvider(tmp_path)], asr=None
    )
    result = service.search_next()
    assert result["material_id"] == "good-001"
    with database.connect() as connection:
        state = connection.execute(
            "SELECT current_state FROM training_progress WHERE material_id = 'keep-001'"
        ).fetchone()["current_state"]
    assert state == "DICTATION_PART_2", "existing training state must survive a failed search"


def test_all_providers_fail_raises_409_not_500(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    service = MaterialSearchService(database, settings, providers=[BoomProvider()], asr=None)
    with pytest.raises(ValueError, match="source failed"):
        service.search_next()

    original_settings = main_module.settings
    main_module.settings = settings
    try:
        with TestClient(main_module.app) as client:
            # Direct provider failure path through the API returns 409.
            client.app.state.material_search = service
            response = client.post("/api/materials/next")
            assert response.status_code == 409
            assert "source failed" in response.json()["detail"]
    finally:
        main_module.settings = original_settings


def test_quality_gate_thresholds(tmp_path: Path) -> None:
    analyzer = AudioQualityAnalyzer()
    good = tmp_path / "good.wav"
    make_sine_wav(good, segments=[(65.0, 12000.0), (1.0, 0.0)])
    assert analyzer.analyze(str(good)).passed is True
    short = tmp_path / "short.wav"
    make_sine_wav(short, segments=[(5.0, 12000.0)])
    assert analyzer.analyze(str(short)).passed is False
