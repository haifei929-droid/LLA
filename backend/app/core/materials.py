from __future__ import annotations

import sqlite3

from app.preprocess.material import MaterialSpec
from app.db.connection import Database


class MaterialExistsError(ValueError):
    """Raised when a material with the same id is created twice."""


class MaterialStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, material: MaterialSpec, *, source: dict | None = None, governance: dict | None = None) -> None:
        """Create a material in its own transaction (preset / legacy path).

        The row is written as QUARANTINED unless the caller supplies verified
        source governance evidence: a material created without source evidence
        stays out of new recommendation, new ingestion and the weekly pool.
        """
        try:
            with self.database.connect() as connection:
                self.insert(connection, material, source=source, governance=governance)
        except sqlite3.IntegrityError as exc:
            raise MaterialExistsError(
                f"Material {material.material_id} already exists; creation is idempotent by id"
            ) from exc

    def insert(
        self,
        connection: sqlite3.Connection,
        material: MaterialSpec,
        *,
        source: dict | None = None,
        governance: dict | None = None,
    ) -> None:
        """Insert material + sentences + initial progress on a caller's connection.

        The caller owns the transaction, so a governed ingestion writes the
        material, its sentences, the initial training progress, the source
        linkage and the ingestion status atomically: either all of it is
        committed or none of it is, and a half-prepared material can never be
        observed.
        """
        source = source or {}
        governance = governance or {}
        connection.execute(
            """
            INSERT INTO materials(
                material_id, title, source_type, source_name, source_url, audio_path,
                transcript, duration_seconds, speech_rate_wpm, status,
                part_1_start, part_1_end, part_2_start, part_2_end,
                part_3_start, part_3_end, created_at, published_at,
                source_candidate_id, speed_stage, prepare_status,
                source_platform, source_course, item_order, accent, cefr_level,
                transcript_verified, license_status, ingestion_status
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                datetime('now'), datetime('now'),
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                material.material_id,
                material.title,
                source.get("source_type", "PRESET"),
                source.get("source_name"),
                source.get("source_url"),
                material.audio_path,
                material.transcript,
                material.duration_seconds,
                material.speech_rate_wpm,
                material.status,
                material.part_boundaries[0][0],
                material.part_boundaries[0][1],
                material.part_boundaries[1][0],
                material.part_boundaries[1][1],
                material.part_boundaries[2][0],
                material.part_boundaries[2][1],
                source.get("source_candidate_id"),
                source.get("speed_stage", "STAGE_1"),
                source.get("prepare_status", "READY"),
                governance.get("source_platform"),
                governance.get("source_course"),
                governance.get("item_order"),
                governance.get("accent"),
                governance.get("cefr_level"),
                int(governance.get("transcript_verified") or 0),
                str(governance.get("license_status") or "UNKNOWN"),
                str(governance.get("ingestion_status") or "QUARANTINED"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO sentences(
                sentence_id, material_id, part_no, sequence_no, text, normalized_text,
                start_time, end_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sentence.sentence_id,
                    sentence.material_id,
                    sentence.part_no,
                    sentence.sequence_no,
                    sentence.text,
                    sentence.normalized_text,
                    sentence.start_time,
                    sentence.end_time,
                )
                for sentence in material.sentences
            ],
        )
        connection.execute(
            "INSERT INTO training_progress(material_id, current_state, updated_at) VALUES (?, 'READY_FIRST_LISTEN', datetime('now'))",
            (material.material_id,),
        )

    def list(self) -> list[dict[str, object]]:
        return self.search("")

    def search(self, query: str) -> list[dict[str, object]]:
        term = f"%{query.strip()}%"
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT m.material_id, m.title, m.duration_seconds, m.speech_rate_wpm, m.status,
                       p.current_state, m.ingestion_status, m.source_platform, m.accent,
                       m.transcript_verified
                  FROM materials m
                  LEFT JOIN training_progress p ON p.material_id = m.material_id
                 WHERE ? = '' OR m.material_id LIKE ? OR m.title LIKE ? OR m.transcript LIKE ?
                 ORDER BY m.created_at DESC
                """,
                (query.strip(), term, term, term),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, material_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            material = connection.execute(
                """
                SELECT m.*, p.current_state, p.dictation_part_status, p.current_sentence_id,
                       p.current_attempt, p.reading_part_status, p.full_reading_status
                  FROM materials m
                  LEFT JOIN training_progress p ON p.material_id = m.material_id
                 WHERE m.material_id = ?
                """,
                (material_id,),
            ).fetchone()
            if material is None:
                return None
            sentences = connection.execute(
                """
                SELECT sentence_id, part_no, sequence_no, text, start_time, end_time
                  FROM sentences
                 WHERE material_id = ?
                 ORDER BY sequence_no
                """,
                (material_id,),
            ).fetchall()
        result = dict(material)
        result["sentences"] = [dict(sentence) for sentence in sentences]
        return result

    def get_sentence(self, material_id: str, sentence_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sentences WHERE material_id = ? AND sentence_id = ?",
                (material_id, sentence_id),
            ).fetchone()
        return dict(row) if row else None
