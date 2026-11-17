from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import Settings
from app.core.dictation import normalize_for_match


SCHEMA_PATH = __file__.replace("connection.py", "schema.sql")


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def initialize(self) -> None:
        self.settings.ensure_directories()
        with self.connect() as connection:
            with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
                connection.executescript(schema_file.read())
            self._migrate_legacy_schema(connection)

    @staticmethod
    def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(dictation_attempts)")}
        if "listen_count" not in columns:
            connection.execute(
                "ALTER TABLE dictation_attempts ADD COLUMN listen_count INTEGER NOT NULL DEFAULT 1"
            )
        if "memory_targets" not in columns:
            connection.execute(
                "ALTER TABLE dictation_attempts ADD COLUMN memory_targets TEXT NOT NULL DEFAULT '[]'"
            )
        operation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(dictation_operations)")}
        if "normalized_text" not in operation_columns:
            connection.execute(
                "ALTER TABLE dictation_operations ADD COLUMN normalized_text TEXT NOT NULL DEFAULT ''"
            )
        Database._backfill_operation_identity(connection)
        material_columns = {row["name"] for row in connection.execute("PRAGMA table_info(materials)")}
        if "source_url" not in material_columns:
            connection.execute("ALTER TABLE materials ADD COLUMN source_url TEXT")
        for column, definition in (
            ("source_candidate_id", "TEXT"),
            ("speed_stage", "TEXT NOT NULL DEFAULT 'STAGE_1'"),
            ("prepare_status", "TEXT NOT NULL DEFAULT 'READY'"),
            # Source governance columns. Legacy rows keep accent NULL and
            # become QUARANTINED: nothing is guessed (no en-GB backfill) and
            # no existing audio, sentence, attempt, progress or generated
            # weekly item is touched.
            ("source_platform", "TEXT"),
            ("source_course", "TEXT"),
            ("item_order", "INTEGER"),
            ("accent", "TEXT"),
            ("cefr_level", "TEXT"),
            ("transcript_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("license_status", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
            ("ingestion_status", "TEXT NOT NULL DEFAULT 'QUARANTINED'"),
        ):
            if column not in material_columns:
                connection.execute(f"ALTER TABLE materials ADD COLUMN {column} {definition}")
        Database._migrate_candidate_governance(connection)
        # Governance lookup indexes are created only after the columns exist:
        # they must never fail the migration of a database written before
        # these columns existed.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_materials_governance "
            "ON materials(ingestion_status, source_platform)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidates_governance "
            "ON material_candidates(scope_id, provider, ingestion_status)"
        )
        Database._ensure_source_identity_index(connection)

    @staticmethod
    def _migrate_candidate_governance(connection: sqlite3.Connection) -> None:
        """Extend the candidate table into the persistent discovery list.

        Each column is additive with a value that means "no evidence yet", so
        a legacy candidate row simply becomes a pending discovery entry
        instead of being rejected or rewritten.
        """
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(material_candidates)")}
        for column, definition in (
            ("source_course", "TEXT"),
            ("course_order", "INTEGER"),
            ("item_order", "INTEGER"),
            ("accent", "TEXT"),
            ("speaker_id", "TEXT"),
            ("cefr_level", "TEXT"),
            ("transcript_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("license_status", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
            ("license_reference", "TEXT"),
            ("ingestion_status", "TEXT NOT NULL DEFAULT 'PENDING'"),
            ("source_priority", "INTEGER"),
            ("transcript_origin", "TEXT"),
            ("accent_evidence", "TEXT"),
            ("transcript_evidence", "TEXT"),
            ("reviewed_by", "TEXT"),
            ("reviewed_at", "TEXT"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("imported_at", "TEXT"),
            ("ingestion_result_json", "TEXT"),
        ):
            if column not in columns:
                connection.execute(f"ALTER TABLE material_candidates ADD COLUMN {column} {definition}")

    @staticmethod
    def _ensure_source_identity_index(connection: sqlite3.Connection) -> None:
        """One formal material per source identity.

        `material_candidates` is the persistent discovery list, so a
        (platform, item id) pair must stay unique: alias URLs, separate
        batches and separate scopes cannot create the same lesson twice, and
        a material links back through `source_candidate_id`. A legacy database
        may already hold duplicates; that history is never deleted, so the
        unique index is skipped and only a lookup index is created, with the
        state recorded in `app_metadata` for review.
        """
        try:
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_material_candidates_source_identity "
                "ON material_candidates(provider, provider_item_id)"
            )
            candidate_state = "ENFORCED"
        except sqlite3.IntegrityError:
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_material_candidates_source_identity "
                "ON material_candidates(provider, provider_item_id)"
            )
            candidate_state = "SKIPPED_DUPLICATES_PRESENT"
        connection.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('source_identity_unique', ?)",
            (candidate_state,),
        )
        try:
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_materials_source_candidate "
                "ON materials(source_candidate_id) WHERE source_candidate_id IS NOT NULL"
            )
            material_state = "ENFORCED"
        except sqlite3.IntegrityError:
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_materials_source_candidate "
                "ON materials(source_candidate_id) WHERE source_candidate_id IS NOT NULL"
            )
            material_state = "SKIPPED_DUPLICATES_PRESENT"
        connection.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('material_source_unique', ?)",
            (material_state,),
        )

    @staticmethod
    def _backfill_operation_identity(connection: sqlite3.Connection) -> None:
        """Recover `normalized_text` for operations written before the column
        existed.

        The first submit's request identity is reconstructable from the attempt
        that submit produced: `result.attempt_number` + `sentence_id` uniquely
        identify the `dictation_attempts` row, whose `user_text` is normalized
        with the same `normalize_for_match` semantics the submit path uses.
        Rows that cannot be reconstructed (missing attempt) are left with the
        empty sentinel rather than fabricated, and re-running the backfill only
        touches still-empty rows, so it is idempotent.
        """
        rows = connection.execute(
            "SELECT operation_id, result FROM dictation_operations WHERE normalized_text = ''"
        ).fetchall()
        for row in rows:
            try:
                result = json.loads(row["result"])
            except (ValueError, TypeError):
                continue
            sentence_id = result.get("sentence_id")
            attempt_number = result.get("attempt_number")
            if sentence_id is None or attempt_number is None:
                continue
            attempt = connection.execute(
                "SELECT user_text FROM dictation_attempts WHERE sentence_id = ? AND attempt_number = ?",
                (sentence_id, attempt_number),
            ).fetchone()
            if attempt is None:
                continue
            connection.execute(
                "UPDATE dictation_operations SET normalized_text = ? WHERE operation_id = ?",
                (normalize_for_match(attempt["user_text"]), row["operation_id"]),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # IMMEDIATE serializes writers so read-then-write sequences (e.g. the
        # next attempt number in a dictation submit) never race under
        # concurrent requests; single-user P0 pays no measurable cost.
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
