"""M3: reading scoring — deterministic analysis, Rule Engine, and gates.

Spec 31.5: each dimension is judged independently (no averaged score), the
same recording re-scored stays stable, and obviously too fast / wrong pauses /
flat stress must be distinguishable. The full-reading assessment may only
complete after a passing three-dimension score exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.adapters.audio import WaveAudioAnalyzer
from app.core.reading_scoring import ReadingRuleEngine
from app.core.reading_service import ReadingService
from app.core.states import TransitionError
from app.core.training_events import TrainingEventService
from app.db.connection import Database
from tests.fixtures import (
    DEFAULT_SENTENCES,
    create_material,
    make_database,
    make_settings,
    make_sine_wav,
)

REFERENCE_SEGMENTS = [(2.0, 12000.0), (0.5, 0.0)]  # 2.5 s, one 0.5 s tail pause


def _material_with_audio(tmp_path: Path, database: Database, material_id: str = "m1") -> str:
    audio = tmp_path / "data" / "materials" / f"{material_id}.wav"
    make_sine_wav(audio, segments=REFERENCE_SEGMENTS)
    create_material(database, material_id)
    with database.connect() as connection:
        connection.execute(
            "UPDATE materials SET audio_path = ? WHERE material_id = ?",
            (str(audio), material_id),
        )
    return material_id


def _drive_to_reading(database: Database, material_id: str) -> None:
    events = TrainingEventService(database)
    events.complete_first_listen(material_id)
    events.submit_comprehension(
        material_id=material_id, phase="FIRST", self_rating="30\u201350%", summary="Reading test."
    )
    from app.core.dictation_service import DictationService

    dictation = DictationService(database)
    for part in (1, 2, 3):
        for index in range((part - 1) * 3, part * 3):
            dictation.submit(
                material_id=material_id,
                sentence_id=f"{material_id}-sentence-{index + 1:03d}",
                user_text=DEFAULT_SENTENCES[index],
                listen_count=1,
            )
        events.complete_dictation_part(material_id, part)
    events.complete_second_listen(material_id)
    events.submit_comprehension(
        material_id=material_id, phase="SECOND", self_rating=">70%", summary="Reading test."
    )


def test_rule_engine_judges_dimensions_independently(tmp_path: Path) -> None:
    reference = WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "ref.wav", REFERENCE_SEGMENTS)))
    engine = ReadingRuleEngine()

    close = engine.score(reference, WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "close.wav", [(2.1, 12000.0), (0.5, 0.0)]))))
    assert close.speed == "PASS"
    assert close.overall_pass is True

    too_fast = engine.score(reference, WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "fast.wav", [(1.0, 12000.0), (0.5, 0.0)]))))
    assert too_fast.speed == "FAIL"

    many_pauses = engine.score(reference, WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "pauses.wav", [
        (1.0, 12000.0), (0.5, 0.0), (1.0, 12000.0), (0.5, 0.0), (0.5, 12000.0), (0.5, 0.0), (0.5, 12000.0), (0.5, 0.0),
    ]))))
    # 4 pauses vs 1 reference pause -> outside the PASS tolerance of 2, inside CLOSE.
    assert many_pauses.pause == "CLOSE"
    assert many_pauses.overall_pass is False

    too_many_pauses = engine.score(reference, WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "toomany.wav", [
        (0.5, 12000.0), (0.5, 0.0), (0.5, 12000.0), (0.5, 0.0), (0.5, 12000.0), (0.5, 0.0),
        (0.5, 12000.0), (0.5, 0.0), (0.5, 12000.0), (0.5, 0.0), (0.5, 12000.0), (0.5, 0.0),
    ]))))
    # 6 pauses vs 1 reference pause -> outside the CLOSE tolerance of 4.
    assert too_many_pauses.pause == "FAIL"

    flat = engine.score(reference, WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "flat.wav", [(2.5, 12000.0)]))))
    # Reference has strong amplitude variation (cv > 0); a flat recording does not.
    assert reference.rms_cv > 0.1
    assert flat.stress == "FAIL"


def test_rule_engine_stability(tmp_path: Path) -> None:
    reference = WaveAudioAnalyzer().analyze(str(make_sine_wav(tmp_path / "ref.wav", REFERENCE_SEGMENTS)))
    recording = str(make_sine_wav(tmp_path / "rec.wav", [(2.05, 12000.0), (0.5, 0.0)]))
    engine = ReadingRuleEngine()
    first = engine.score(reference, WaveAudioAnalyzer().analyze(recording))
    second = engine.score(reference, WaveAudioAnalyzer().analyze(recording))
    assert (first.speed, first.pause, first.stress) == (second.speed, second.pause, second.stress)


def test_reading_part_requires_passing_score_before_complete(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    material_id = _material_with_audio(tmp_path, database)
    _drive_to_reading(database, material_id)
    events = TrainingEventService(database)

    with pytest.raises(TransitionError, match="scoring"):
        events.complete_reading_part(material_id, 1)

    service = ReadingService(database, make_settings(tmp_path))
    recording = make_sine_wav(tmp_path / "part1.wav", [(2.0, 12000.0), (0.5, 0.0)])
    result = service.score(
        material_id=material_id, scope="PART", part_no=1, recording_path=recording
    )
    assert result["overall_pass"] is True
    snapshot = events.complete_reading_part(material_id, 1)
    assert snapshot.current_state.value == "READING_AVAILABLE"
    assert snapshot.reading_part_status == {"1": True, "2": False, "3": False}


def test_full_reading_assessment_requires_passing_score(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    material_id = _material_with_audio(tmp_path, database)
    _drive_to_reading(database, material_id)
    events = TrainingEventService(database)
    service = ReadingService(database, make_settings(tmp_path))

    for part in (1, 2, 3):
        recording = make_sine_wav(tmp_path / f"part{part}.wav", [(2.0, 12000.0), (0.5, 0.0)])
        assert service.score(material_id=material_id, scope="PART", part_no=part, recording_path=recording)["overall_pass"]
        snapshot = events.complete_reading_part(material_id, part)
    assert snapshot.current_state.value == "FULL_READING_ASSESSMENT"

    with pytest.raises(TransitionError, match="scoring"):
        events.complete_full_reading_assessment(material_id, True)

    full_recording = make_sine_wav(tmp_path / "full.wav", [(2.05, 12000.0), (0.5, 0.0)])
    full = service.score(material_id=material_id, scope="FULL", part_no=None, recording_path=full_recording)
    assert full["overall_pass"] is True
    assert events.complete_full_reading_assessment(material_id, True).current_state.value == "FULLY_COMPLETED"


def test_scoring_is_locked_outside_reading_states(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    material_id = _material_with_audio(tmp_path, database)
    service = ReadingService(database, make_settings(tmp_path))
    recording = make_sine_wav(tmp_path / "early.wav", [(2.0, 12000.0), (0.5, 0.0)])
    with pytest.raises(TransitionError):
        service.score(material_id=material_id, scope="PART", part_no=1, recording_path=recording)


def test_score_api_round_trip(tmp_path: Path) -> None:
    import base64

    database = make_database(tmp_path)
    material_id = _material_with_audio(tmp_path, database, "api-m1")
    _drive_to_reading(database, material_id)

    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            recording = make_sine_wav(tmp_path / "api-part1.wav", [(2.05, 12000.0), (0.5, 0.0)])
            encoded = base64.b64encode(recording.read_bytes()).decode("ascii")
            response = client.post(
                f"/api/materials/{material_id}/reading-parts/1/score",
                json={"filename": "part1.wav", "content_base64": encoded},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["overall_pass"] is True
            assert payload["speed"] == "PASS"
            assert payload["scope"] == "PART"

            complete = client.post(f"/api/materials/{material_id}/reading-parts/1/complete")
            assert complete.status_code == 200, complete.text
            assert complete.json()["reading_part_status"]["1"] is True
    finally:
        main_module.settings = original_settings
