from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.core.dictation_service import DictationService
from app.core.materials import MaterialStore
from app.core.progress import TrainingProgressStore
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence


def _setup(tmp_path: Path) -> tuple[Database, MaterialStore, str]:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )
    database = Database(settings)
    database.initialize()
    material = MaterialPreprocessor().process(
        material_id="m1",
        title="Preset",
        audio_path="m1.wav",
        transcript="One sentence. Two sentence. Three sentence.",
        timestamped_sentences=[
            TimestampedSentence("One sentence.", 0, 10),
            TimestampedSentence("Two sentence.", 10, 20),
            TimestampedSentence("Three sentence.", 20, 30),
        ],
    )
    store = MaterialStore(database)
    store.create(material)
    progress = TrainingProgressStore(database)
    progress.transition("m1", "first_full_listen_completed")
    progress.transition("m1", "comprehension_submitted")
    return database, store, material.sentences[0].sentence_id


def test_material_and_dictation_services_persist_attempts(tmp_path: Path) -> None:
    database, store, sentence_id = _setup(tmp_path)
    service = DictationService(database)

    first = service.submit(
        material_id="m1",
        sentence_id=sentence_id,
        user_text="One ____",
        listen_count=1,
        hint_level=1,
        operation_id=f"op-{uuid4().hex}",
    )
    second = service.submit(
        material_id="m1",
        sentence_id=sentence_id,
        user_text="One sentence.",
        listen_count=2,
        operation_id=f"op-{uuid4().hex}",
    )

    assert store.list()[0]["material_id"] == "m1"
    assert first["attempt_number"] == 1
    assert second["attempt_number"] == 2
    assert second["is_exact_match"] is True
    with database.connect() as connection:
        row = connection.execute(
            "SELECT current_sentence_id, current_attempt FROM training_progress WHERE material_id = 'm1'"
        ).fetchone()
    assert row["current_sentence_id"] == sentence_id
    assert row["current_attempt"] == 2
    with database.connect() as connection:
        memory = connection.execute(
            "SELECT target, encounter_count, first_listen_correct_count FROM listening_memory WHERE target = 'sentence'"
        ).fetchone()
    assert memory["encounter_count"] == 2
    assert '2' in memory["first_listen_correct_count"]


def test_exact_first_attempt_does_not_create_listening_memory(tmp_path: Path) -> None:
    database, _, sentence_id = _setup(tmp_path)
    service = DictationService(database)

    service.submit(
        material_id="m1",
        sentence_id=sentence_id,
        user_text="One sentence.",
        listen_count=1,
        operation_id=f"op-{uuid4().hex}",
    )

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM listening_memory").fetchone()["count"]
    assert count == 0
