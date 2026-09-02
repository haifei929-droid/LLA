from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.core.dictation_service import DictationService
from app.core.materials import MaterialStore
from app.core.reading_service import ReadingService
from app.core.states import MaterialState, TransitionError
from app.core.training_events import TrainingEventService
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence
from tests.fixtures import make_settings, make_sine_wav


def _setup(tmp_path: Path) -> tuple[Database, list[str]]:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )
    database = Database(settings)
    database.initialize()
    timestamped = [
        TimestampedSentence(f"Sentence {index}.", (index - 1) * 10, index * 10)
        for index in range(1, 10)
    ]
    material = MaterialPreprocessor().process(
        material_id="m1",
        title="Preset",
        audio_path=str(tmp_path / "m1.wav"),
        transcript=" ".join(sentence.text for sentence in timestamped),
        timestamped_sentences=timestamped,
    )
    MaterialStore(database).create(material)
    make_sine_wav(tmp_path / "m1.wav", segments=[(10.0, 12000.0), (0.5, 0.0)])
    return database, [sentence.sentence_id for sentence in material.sentences]


def _pass_reading_part(database: Database, tmp_path: Path, part_no: int) -> None:
    scoring = ReadingService(database, make_settings(tmp_path))
    recording = make_sine_wav(tmp_path / f"rec-{part_no}.wav", segments=[(10.0, 12000.0), (0.5, 0.0)])
    result = scoring.score(material_id="m1", scope="PART", part_no=part_no, recording_path=recording)
    assert result["overall_pass"] is True


def test_training_event_service_reaches_full_completion(tmp_path: Path) -> None:
    database, sentence_ids = _setup(tmp_path)
    events = TrainingEventService(database)
    dictation = DictationService(database)

    assert events.complete_first_listen("m1").current_state == MaterialState.FIRST_COMPREHENSION_CHECK
    assert events.submit_comprehension(
        material_id="m1", phase="FIRST", self_rating="30–50%", summary="Main idea is clear."
    ).current_state == MaterialState.DICTATION_PART_1

    with pytest.raises(ValueError, match="order"):
        dictation.submit(
            material_id="m1", sentence_id=sentence_ids[1], user_text="Sentence 2.", listen_count=1,
            operation_id=f"op-{uuid4().hex}",
        )
    with pytest.raises(TransitionError):
        events.complete_dictation_part("m1", 2)

    expected_texts = [f"Sentence {index}." for index in range(1, 10)]
    last = None
    for index, sentence_id in enumerate(sentence_ids[:3]):
        dictation.submit(
            material_id="m1", sentence_id=sentence_id, user_text="wrong", listen_count=1,
            operation_id=f"op-{uuid4().hex}",
        )
        last = dictation.submit(
            material_id="m1",
            sentence_id=sentence_id,
            user_text=expected_texts[index],
            listen_count=2,
            operation_id=f"op-{uuid4().hex}",
        )
    assert last["transition_type"] == "PART_COMPLETED"
    assert last["next_state"] == MaterialState.DICTATION_PART_2.value

    for part_no, offset in ((2, 3), (3, 6)):
        last = None
        for index in range(offset, offset + 3):
            last = dictation.submit(
                material_id="m1",
                sentence_id=sentence_ids[index],
                user_text=f"Sentence {index + 1}.",
                listen_count=1,
                operation_id=f"op-{uuid4().hex}",
            )
        assert last["transition_type"] == "PART_COMPLETED"

    assert events.complete_second_listen("m1").current_state == MaterialState.SECOND_COMPREHENSION_CHECK
    assert events.submit_comprehension(
        material_id="m1", phase="SECOND", self_rating=">70%", summary="The details are clear."
    ).current_state == MaterialState.READING_AVAILABLE

    _pass_reading_part(database, tmp_path, 1)
    assert events.complete_reading_part("m1", 1).current_state == MaterialState.READING_AVAILABLE
    _pass_reading_part(database, tmp_path, 2)
    events.complete_reading_part("m1", 2)
    _pass_reading_part(database, tmp_path, 3)
    assert events.complete_reading_part("m1", 3).current_state == MaterialState.FULL_READING_ASSESSMENT
    assert events.complete_full_reading_assessment("m1", False).current_state == MaterialState.FULL_READING_ASSESSMENT
    full_recording = make_sine_wav(tmp_path / "rec-full.wav", segments=[(10.0, 12000.0), (0.5, 0.0)])
    full = ReadingService(database, make_settings(tmp_path)).score(
        material_id="m1", scope="FULL", part_no=None, recording_path=full_recording
    )
    assert full["overall_pass"] is True
    assert events.complete_full_reading_assessment("m1", True).current_state == MaterialState.FULLY_COMPLETED


def test_dictation_is_locked_until_first_comprehension(tmp_path: Path) -> None:
    database, sentence_ids = _setup(tmp_path)
    dictation = DictationService(database)

    with pytest.raises(ValueError, match="not available"):
        dictation.submit(
            material_id="m1", sentence_id=sentence_ids[0], user_text="Sentence 1.", listen_count=1,
            operation_id=f"op-{uuid4().hex}",
        )
