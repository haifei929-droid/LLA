from pathlib import Path

import pytest

from app.config import Settings
from app.core.progress import TrainingProgressStore
from app.core.states import MaterialState, TransitionError
from app.db.connection import Database


@pytest.fixture
def progress_store(tmp_path: Path) -> TrainingProgressStore:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )
    database = Database(settings)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO materials(
                material_id, title, source_type, audio_path, transcript, duration_seconds,
                status, created_at
            ) VALUES ('m1', 'Test', 'PRESET', 'test.wav', 'Hello.', 1, 'READY', 'now')
            """
        )
    return TrainingProgressStore(database)


def test_progress_follows_material_flow(progress_store: TrainingProgressStore) -> None:
    snapshot = progress_store.ensure("m1")
    assert snapshot.current_state == MaterialState.MATERIAL_CREATED

    for event, expected in [
        ("material_prepared", MaterialState.READY_FIRST_LISTEN),
        ("first_full_listen_completed", MaterialState.FIRST_COMPREHENSION_CHECK),
        ("comprehension_submitted", MaterialState.DICTATION_PART_1),
        ("dictation_part_1_completed", MaterialState.DICTATION_PART_2),
        ("dictation_part_2_completed", MaterialState.DICTATION_PART_3),
        ("dictation_part_3_completed", MaterialState.SECOND_FULL_LISTEN),
        ("second_full_listen_completed", MaterialState.SECOND_COMPREHENSION_CHECK),
        ("comprehension_submitted", MaterialState.READING_AVAILABLE),
    ]:
        snapshot = progress_store.transition("m1", event)
        assert snapshot.current_state == expected

    snapshot = progress_store.complete_reading_part("m1", 1)
    assert snapshot.current_state == MaterialState.READING_AVAILABLE
    progress_store.complete_reading_part("m1", 2)
    snapshot = progress_store.complete_reading_part("m1", 3)
    assert snapshot.current_state == MaterialState.FULL_READING_ASSESSMENT
    snapshot = progress_store.transition("m1", "full_reading_passed")
    assert snapshot.current_state == MaterialState.FULLY_COMPLETED


def test_illegal_transition_is_rejected(progress_store: TrainingProgressStore) -> None:
    progress_store.ensure("m1")
    with pytest.raises(TransitionError):
        progress_store.transition("m1", "dictation_part_1_completed")


def test_reading_can_be_skipped_after_listening(progress_store: TrainingProgressStore) -> None:
    progress_store.ensure("m1")
    for event in (
        "material_prepared",
        "first_full_listen_completed",
        "comprehension_submitted",
        "dictation_part_1_completed",
        "dictation_part_2_completed",
        "dictation_part_3_completed",
        "second_full_listen_completed",
        "comprehension_submitted",
        "skip_reading",
    ):
        snapshot = progress_store.transition("m1", event)
    assert snapshot.current_state == MaterialState.LISTENING_COMPLETED
    assert snapshot.full_reading_status == "SKIPPED"

