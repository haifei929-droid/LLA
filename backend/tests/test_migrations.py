from pathlib import Path
import sqlite3

from app.config import Settings
from app.db.connection import Database


def _settings(tmp_path: Path, db_name: str) -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=tmp_path / db_name,
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )


def test_legacy_dictation_schema_is_migrated(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "legacy.sqlite3")
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


def test_governance_migration_is_additive_and_guesses_no_accent(tmp_path: Path) -> None:
    """A pre-governance database keeps every row and is never promoted."""
    settings = _settings(tmp_path, "governance.sqlite3")
    settings.ensure_directories()
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE materials(
                material_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL,
                source_name TEXT, source_url TEXT, audio_path TEXT NOT NULL,
                transcript TEXT NOT NULL, duration_seconds REAL NOT NULL, speech_rate_wpm REAL,
                difficulty_level TEXT, status TEXT NOT NULL, part_1_start REAL, part_1_end REAL,
                part_2_start REAL, part_2_end REAL, part_3_start REAL, part_3_end REAL,
                created_at TEXT NOT NULL, published_at TEXT
            );
            CREATE TABLE sentences(
                sentence_id TEXT PRIMARY KEY, material_id TEXT NOT NULL, part_no INTEGER NOT NULL,
                sequence_no INTEGER NOT NULL, text TEXT NOT NULL, normalized_text TEXT NOT NULL,
                start_time REAL NOT NULL, end_time REAL NOT NULL
            );
            CREATE TABLE training_progress(
                material_id TEXT PRIMARY KEY, current_state TEXT NOT NULL,
                dictation_part_status TEXT NOT NULL DEFAULT '{"1": false, "2": false, "3": false}',
                current_sentence_id TEXT, current_attempt INTEGER NOT NULL DEFAULT 0,
                reading_part_status TEXT NOT NULL DEFAULT '{"1": false, "2": false, "3": false}',
                full_reading_status TEXT NOT NULL DEFAULT 'LOCKED',
                updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        connection.execute(
            """
            INSERT INTO materials(
                material_id, title, source_type, source_name, source_url, audio_path,
                transcript, duration_seconds, speech_rate_wpm, status, created_at
            ) VALUES ('legacy-1', 'Legacy', 'PRESET', 'BBC', 'https://legacy.example/1',
                      'data/materials/legacy-1.wav', 'Legacy transcript.', 900.0, 110.0,
                      'READY', '2026-08-01T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO sentences(
                sentence_id, material_id, part_no, sequence_no, text, normalized_text,
                start_time, end_time
            ) VALUES ('legacy-1-sentence-001', 'legacy-1', 1, 1, 'Legacy sentence one.',
                      'legacy sentence one.', 0.0, 5.0)
            """
        )
        connection.execute(
            """
            INSERT INTO training_progress(material_id, current_state, updated_at)
            VALUES ('legacy-1', 'DICTATION_PART_2', '2026-08-02T00:00:00+00:00')
            """
        )
        connection.commit()
    finally:
        connection.close()

    database = Database(settings)
    database.initialize()
    database.initialize()  # repeated migration stays additive

    with database.connect() as connection:
        material = connection.execute(
            "SELECT * FROM materials WHERE material_id = 'legacy-1'"
        ).fetchone()
        sentences = connection.execute(
            "SELECT text FROM sentences WHERE material_id = 'legacy-1'"
        ).fetchall()
        progress = connection.execute(
            "SELECT current_state FROM training_progress WHERE material_id = 'legacy-1'"
        ).fetchone()
        candidate_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(material_candidates)")
        }
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM app_metadata")
        }
    assert material["title"] == "Legacy"
    assert material["audio_path"] == "data/materials/legacy-1.wav"
    assert material["transcript"] == "Legacy transcript."
    assert material["accent"] is None, "no accent may be invented for legacy material"
    assert material["transcript_verified"] == 0
    assert material["license_status"] == "UNKNOWN"
    assert material["ingestion_status"] == "QUARANTINED"
    assert [row["text"] for row in sentences] == ["Legacy sentence one."]
    assert progress["current_state"] == "DICTATION_PART_2"
    assert {
        "source_course",
        "course_order",
        "item_order",
        "accent",
        "speaker_id",
        "cefr_level",
        "transcript_verified",
        "license_status",
        "ingestion_status",
        "transcript_origin",
        "ingestion_result_json",
    }.issubset(candidate_columns)
    assert metadata["source_identity_unique"] == "ENFORCED"
    assert metadata["material_source_unique"] == "ENFORCED"
