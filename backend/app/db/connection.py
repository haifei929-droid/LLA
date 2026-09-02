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
        ):
            if column not in material_columns:
                connection.execute(f"ALTER TABLE materials ADD COLUMN {column} {definition}")

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
