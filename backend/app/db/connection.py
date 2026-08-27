from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import Settings


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
