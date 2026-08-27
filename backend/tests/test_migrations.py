from pathlib import Path

from app.config import Settings
from app.db.connection import Database


def test_legacy_dictation_schema_is_migrated(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "legacy.sqlite3",
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )
    database = Database(settings)
    settings.ensure_directories()
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE dictation_attempts(
                attempt_id TEXT PRIMARY KEY,
                sentence_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                user_text TEXT NOT NULL,
                is_exact_match INTEGER NOT NULL,
                hint_level INTEGER NOT NULL,
                revealed INTEGER NOT NULL,
                error_details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    database.initialize()

    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(dictation_attempts)")}
    assert {"listen_count", "memory_targets"}.issubset(columns)

