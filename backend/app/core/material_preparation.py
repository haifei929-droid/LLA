"""P1 material selection and preparation (6.2).

A candidate becomes a formal Material only after the user selects it and
prepare succeeds. Prepare is governed (source governance plan):

- only the next qualified item of the active platform can be ingested, so
  selecting a later candidate cannot bypass the course order;
- accent, license, CEFR and transcript evidence are re-checked at ingestion
  time; en-US or unknown/unverified material never enters the formal library;
- an idempotent replay returns the original result before any expiry or
  source-switch check, so a lost response can be retried safely later;
- the material, its sentences, the initial progress, the source linkage and
  the ingestion status are committed in one transaction, and a failure leaves
  the candidate recoverable with a failure code (no half material).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from app.config import Settings
from app.core.material_candidates import (
    CEFR_ORDER,
    ELIGIBLE,
    INGESTED,
    LICENSE_CLEARED,
    PENDING_REVIEW,
    REJECTED,
    SOURCE_LABELS,
    MaterialCandidateService,
    confirmed_learning_level,
    is_british_accent,
    is_level_allowed,
    material_id_for,
    normalize_license,
)
from app.core.materials import MaterialStore
from app.core.transcript_validator import decode_timestamped
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence

#: Stable error codes (P1 8).
CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"
CANDIDATE_NOT_SELECTABLE = "CANDIDATE_NOT_SELECTABLE"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
NO_QUALIFIED_CANDIDATE = "NO_QUALIFIED_CANDIDATE"


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
        history=None,
        candidates: MaterialCandidateService | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.store = MaterialStore(database)
        self.history = history
        self.governance = candidates or MaterialCandidateService(database, settings, now_fn=self.now_fn)

    def prepare(self, candidate_id: str, scope_id: str, idempotency_key: str) -> dict[str, object]:
        if not idempotency_key.strip():
            raise MaterialSelectionError(IDEMPOTENCY_CONFLICT, "idempotency_key is required")
        # Idempotent replay is decided before expiry/source checks so a lost
        # response can be retried safely later. It is decided again inside the
        # writing transaction below.
        with self.database.connect() as connection:
            candidate = self._candidate(connection, candidate_id, scope_id)
            replayed = self._replay(connection, candidate, idempotency_key)
            if replayed is not None:
                return replayed
            if candidate["idempotency_key"] and candidate["idempotency_key"] != idempotency_key:
                raise MaterialSelectionError(
                    IDEMPOTENCY_CONFLICT, "candidate was selected with a different key"
                )
            stage = str(candidate["speed_stage"])

        result: dict[str, object] | None = None
        material_id: str | None = None
        try:
            # One write transaction: the candidate row is re-read and every
            # governance check is re-run against the *latest* active cycle,
            # review state and course order, then the material is built from
            # that same row and committed together with its ingestion state.
            # There is no second, differently-validated window.
            with self.database.connect() as connection:
                current = self._candidate(connection, candidate_id, scope_id)
                replayed = self._replay(connection, current, idempotency_key)
                if replayed is not None:
                    return replayed
                if current["idempotency_key"] and current["idempotency_key"] != idempotency_key:
                    raise MaterialSelectionError(
                        IDEMPOTENCY_CONFLICT, "candidate was selected with a different key"
                    )
                if _is_expired(current["expires_at"], self.now_fn()):
                    raise MaterialSelectionError(
                        CANDIDATE_EXPIRED, "candidate has expired; search again"
                    )
                self._assert_eligible(current, scope_id, connection)
                self._assert_course_order(current, scope_id, connection)
                try:
                    aligned = decode_timestamped(current["timestamped_sentences_json"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise MaterialSelectionError(
                        CANDIDATE_NOT_SELECTABLE, f"candidate timestamps are unusable: {exc}"
                    ) from exc
                if not aligned:
                    raise MaterialSelectionError(
                        CANDIDATE_NOT_SELECTABLE, "candidate has no aligned sentences"
                    )
                stage = str(current["speed_stage"])
                existing = self._existing_material(connection, current)
                if existing is not None:
                    self._mark_ingested(connection, current, existing, idempotency_key, reused=True)
                    return {
                        "material_id": existing,
                        "prepare_status": "READY",
                        "reused": True,
                    }
                material = MaterialPreprocessor().process(
                    material_id=material_id_for(
                        str(current["provider"]), str(current["provider_item_id"])
                    ),
                    title=current["title"],
                    audio_path=current["audio_path"],
                    transcript=current["transcript"],
                    timestamped_sentences=[
                        TimestampedSentence(text=text, start_time=start, end_time=end)
                        for text, start, end in aligned
                    ],
                )
                self.store.insert(
                    connection,
                    material,
                    source={
                        "source_type": current["provider"],
                        "source_name": SOURCE_LABELS.get(
                            str(current["provider"]), str(current["provider"])
                        ),
                        "source_url": current["source_url"],
                        "source_candidate_id": candidate_id,
                        "speed_stage": current["speed_stage"],
                        "prepare_status": "READY",
                    },
                    governance={
                        "source_platform": current["provider"],
                        "source_course": current["source_course"],
                        "item_order": current["item_order"],
                        "accent": current["accent"],
                        "cefr_level": current["cefr_level"],
                        "transcript_verified": current["transcript_verified"],
                        "license_status": current["license_status"],
                        "ingestion_status": "INGESTED",
                    },
                )
                self._mark_ingested(connection, current, material.material_id, idempotency_key)
                material_id = material.material_id
                result = {
                    "material_id": material.material_id,
                    "prepare_status": "READY",
                    "reused": False,
                }
        except MaterialSelectionError:
            raise
        except Exception as exc:
            # The transaction rolled back: no material, no sentences, no
            # progress. The candidate stays recoverable with a failure code.
            self._record_failure(candidate_id, scope_id, exc)
            raise MaterialSelectionError(
                "PREPARE_FAILED", f"prepare failed; candidate kept recoverable: {exc}"
            ) from exc

        if self.history is not None and material_id is not None:
            self.history.record(
                "default" if scope_id is None else scope_id,
                "MATERIAL_PREPARED",
                stage_before=stage,
                stage_after=stage,
                source_record_ids=[candidate_id, material_id],
                reason="candidate prepared into a READY material",
            )
        return result  # type: ignore[return-value]

    def prepare_next(self, scope_id: str, idempotency_key: str) -> dict[str, object]:
        """Ingest the next qualified item of the active source, nothing else."""
        candidate = self.governance.next_qualified(scope_id)
        if candidate is None:
            raise MaterialSelectionError(
                NO_QUALIFIED_CANDIDATE,
                "no qualified candidate is waiting for the active source",
            )
        return self.prepare(candidate["candidate_id"], scope_id, idempotency_key)

    # ------------------------------------------------------------------
    # Governance checks
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate(connection, candidate_id: str, scope_id: str):
        candidate = connection.execute(
            "SELECT * FROM material_candidates WHERE candidate_id = ? AND scope_id = ?",
            (candidate_id, scope_id),
        ).fetchone()
        if candidate is None:
            raise MaterialSelectionError(CANDIDATE_NOT_FOUND, f"no candidate {candidate_id}")
        return candidate

    def _assert_eligible(self, candidate, scope_id: str, connection) -> None:
        active = self.governance.active_source(scope_id, connection=connection)
        if active is None:
            raise MaterialSelectionError("SOURCE_NOT_APPROVED", "no active source; nothing may be ingested")
        if candidate["provider"] != active["platform"]:
            raise MaterialSelectionError(
                "SOURCE_NOT_APPROVED", "candidate belongs to a non-active source"
            )
        if not is_british_accent(candidate["accent"]):
            raise MaterialSelectionError(
                "ACCENT_NOT_ELIGIBLE",
                "only the canonical en-GB accent can enter the formal training library",
            )
        # Discovery level is not learning eligibility: the confirmed CEFR
        # ceiling of the existing eight-week mechanism caps what may be
        # ingested, never a client-supplied stage. Checked before the queue
        # state so a deferred higher-level item reports the real reason.
        ceiling = confirmed_learning_level(connection, scope_id)
        if str(candidate["cefr_level"] or "").upper() in CEFR_ORDER and not is_level_allowed(
            candidate["cefr_level"], ceiling
        ):
            raise MaterialSelectionError(
                "LEARNING_LEVEL_NOT_CONFIRMED",
                f"candidate level {candidate['cefr_level']} exceeds the confirmed "
                f"learning level {ceiling}; the eight-week confirmation must unlock it first",
            )
        status = candidate["ingestion_status"]
        if status == PENDING_REVIEW:
            raise MaterialSelectionError(
                "SOURCE_PENDING_REVIEW", f"candidate awaits review ({candidate['failure_code']})"
            )
        if status == REJECTED:
            raise MaterialSelectionError(
                CANDIDATE_NOT_SELECTABLE,
                f"candidate status {candidate['candidate_status']} / {status}",
            )
        if status != ELIGIBLE:
            raise MaterialSelectionError(
                CANDIDATE_NOT_SELECTABLE, f"candidate ingestion status {status}"
            )
        if candidate["candidate_status"] not in ("CANDIDATE", "SELECTED"):
            raise MaterialSelectionError(
                CANDIDATE_NOT_SELECTABLE, f"candidate status {candidate['candidate_status']}"
            )
        if candidate["audio_quality"] == "Poor":
            raise MaterialSelectionError(CANDIDATE_NOT_SELECTABLE, "candidate quality is Poor")
        if normalize_license(candidate["license_status"]) != LICENSE_CLEARED:
            raise MaterialSelectionError("LICENSE_UNVERIFIED", "source license is not cleared")
        if str(candidate["cefr_level"] or "").upper() not in CEFR_ORDER:
            raise MaterialSelectionError("CEFR_UNVERIFIED", "candidate CEFR level is not verified")
        if not candidate["transcript_verified"]:
            raise MaterialSelectionError(
                "TRANSCRIPT_UNVERIFIED", "transcript is not verified for this candidate"
            )

    def _assert_course_order(self, candidate, scope_id: str, connection) -> None:
        qualified = self.governance.next_qualified(scope_id, connection=connection)
        if qualified is None:
            raise MaterialSelectionError(
                NO_QUALIFIED_CANDIDATE, "no qualified candidate is awaiting ingestion"
            )
        if qualified["candidate_id"] != candidate["candidate_id"]:
            raise MaterialSelectionError(
                "COURSE_ORDER_VIOLATION",
                "only the next qualified item in platform course order may be ingested",
            )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _replay(connection, candidate, idempotency_key: str) -> dict[str, object] | None:
        """Idempotent replay: the original result survives expiry and switching."""
        if candidate["idempotency_key"] != idempotency_key:
            return None
        stored = candidate["ingestion_result_json"]
        if stored:
            try:
                payload = json.loads(stored)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("material_id"):
                return {**payload, "reused": True}
        if candidate["candidate_status"] != "SELECTED":
            return None
        row = connection.execute(
            "SELECT material_id FROM materials WHERE source_candidate_id = ?",
            (candidate["candidate_id"],),
        ).fetchone()
        if row is None:
            return None
        return {"material_id": row["material_id"], "prepare_status": "READY", "reused": True}

    @staticmethod
    def _existing_material(connection, candidate) -> str | None:
        """One formal material per source identity, including legacy duplicates."""
        row = connection.execute(
            "SELECT material_id FROM materials WHERE source_candidate_id = ?",
            (candidate["candidate_id"],),
        ).fetchone()
        if row is not None:
            return row["material_id"]
        row = connection.execute(
            """
            SELECT m.material_id
              FROM materials m
              JOIN material_candidates c ON c.candidate_id = m.source_candidate_id
             WHERE c.provider = ? AND c.provider_item_id = ?
             LIMIT 1
            """,
            (candidate["provider"], candidate["provider_item_id"]),
        ).fetchone()
        return None if row is None else row["material_id"]

    def _mark_ingested(
        self,
        connection,
        candidate,
        material_id: str,
        idempotency_key: str,
        reused: bool = False,
    ) -> None:
        """Record the ingestion on the candidate row (the single state record).

        Called inside the ingestion transaction, so the candidate state and the
        formal material are committed together. There is no second per-item
        ledger that could disagree with the material.
        """
        payload = {
            "material_id": material_id,
            "prepare_status": "READY",
            "reused": reused,
        }
        now = self.now_fn()
        connection.execute(
            """
            UPDATE material_candidates
               SET candidate_status = 'SELECTED', ingestion_status = ?,
                   idempotency_key = ?, imported_at = ?, ingestion_result_json = ?,
                   failure_code = NULL, attempt_count = attempt_count + 1
             WHERE candidate_id = ?
            """,
            (
                INGESTED,
                idempotency_key,
                now.isoformat(),
                json.dumps(payload, sort_keys=True),
                candidate["candidate_id"],
            ),
        )
        connection.execute("UPDATE materials SET ingestion_status = 'INGESTED' WHERE material_id = ? AND ingestion_status <> 'INGESTED'", (material_id,))

    def _record_failure(self, candidate_id: str, scope_id: str, exc: Exception) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE material_candidates
                   SET failure_code = ?, ingestion_status = ?, attempt_count = attempt_count + 1
                 WHERE candidate_id = ? AND scope_id = ?
                """,
                (f"PREPARE_FAILED:{type(exc).__name__}", ELIGIBLE, candidate_id, scope_id),
            )


def _is_expired(value: object, now: datetime) -> bool:
    if not value:
        return False
    try:
        expires = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires < now
