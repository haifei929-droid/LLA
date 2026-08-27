"""P1 material selection and preparation (6.2).

A candidate becomes a formal Material only after the user selects it and
prepare succeeds. Prepare is idempotent (idempotency_key), failures are
recoverable (candidate stays selectable with a failure_code), and a formal
material is only created on success with prepare_status READY.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from app.config import Settings
from app.core.materials import MaterialStore
from app.core.transcript_validator import decode_timestamped
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence

#: Stable error codes (P1 8).
CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"
CANDIDATE_NOT_SELECTABLE = "CANDIDATE_NOT_SELECTABLE"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


class MaterialSelectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MaterialPreparationService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.store = MaterialStore(database)

    def prepare(self, candidate_id: str, scope_id: str, idempotency_key: str) -> dict[str, object]:
        if not idempotency_key.strip():
            raise MaterialSelectionError(IDEMPOTENCY_CONFLICT, "idempotency_key is required")
        with self.database.connect() as connection:
            candidate = connection.execute(
                "SELECT * FROM material_candidates WHERE candidate_id = ? AND scope_id = ?",
                (candidate_id, scope_id),
            ).fetchone()
            if candidate is None:
                raise MaterialSelectionError(CANDIDATE_NOT_FOUND, f"no candidate {candidate_id}")
            if datetime.fromisoformat(candidate["expires_at"]) < self.now_fn():
                raise MaterialSelectionError(CANDIDATE_EXPIRED, "candidate has expired; search again")
            if candidate["candidate_status"] not in ("CANDIDATE", "SELECTED"):
                raise MaterialSelectionError(CANDIDATE_NOT_SELECTABLE, f"candidate status {candidate['candidate_status']}")
            if candidate["audio_quality"] == "Poor":
                raise MaterialSelectionError(CANDIDATE_NOT_SELECTABLE, "candidate quality is Poor")
            if candidate["idempotency_key"] and candidate["idempotency_key"] != idempotency_key:
                raise MaterialSelectionError(IDEMPOTENCY_CONFLICT, "candidate was selected with a different key")

            # Idempotent re-entry: an already-prepared candidate returns the
            # existing material instead of creating a duplicate.
            existing = connection.execute(
                "SELECT material_id FROM materials WHERE source_candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "material_id": existing["material_id"],
                    "prepare_status": "READY",
                    "reused": True,
                }

            connection.execute(
                "UPDATE material_candidates SET candidate_status = 'SELECTED', idempotency_key = ? WHERE candidate_id = ?",
                (idempotency_key, candidate_id),
            )

        try:
            aligned = decode_timestamped(candidate["timestamped_sentences_json"])
            if not aligned:
                raise ValueError("candidate has no aligned sentences")
            material = MaterialPreprocessor().process(
                material_id=candidate["provider_item_id"],
                title=candidate["title"],
                audio_path=candidate["audio_path"],
                transcript=candidate["transcript"],
                timestamped_sentences=[
                    TimestampedSentence(text=text, start_time=start, end_time=end)
                    for text, start, end in aligned
                ],
            )
            self.store.create(material)
        except Exception as exc:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE material_candidates SET failure_code = ?, candidate_status = 'CANDIDATE' WHERE candidate_id = ?",
                    (f"PREPARE_FAILED:{type(exc).__name__}", candidate_id),
                )
            raise MaterialSelectionError(
                "PREPARE_FAILED", f"prepare failed; candidate kept recoverable: {exc}"
            ) from exc

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE materials
                   SET source_candidate_id = ?, speed_stage = ?, prepare_status = 'READY'
                 WHERE material_id = ?
                """,
                (candidate_id, candidate["speed_stage"], material.material_id),
            )
        return {
            "material_id": material.material_id,
            "prepare_status": "READY",
            "reused": False,
        }
