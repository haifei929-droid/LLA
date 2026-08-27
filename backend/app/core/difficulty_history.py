"""P2-3 difficulty progression history (P2 spec 11).

Append-only, user-observable timeline. Events are immutable and idempotent
via a stable (scope, event_type, occurred_at) key. Formal Stage facts come
only from P1 records; the legacy /api/materials/next path never creates or
alters events here. Downgrade is always suggestion -> explicit confirmation,
crosses at most one Stage, and resets the eight-week counter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.db.connection import Database

EVENT_TYPES = (
    "WEEKLY_GATE_RECORDED", "STREAK_UPDATED", "UPGRADE_ELIGIBLE", "UPGRADE_PROMPTED",
    "UPGRADE_DECIDED", "COOLDOWN_STARTED", "COOLDOWN_EXPIRED", "STAGE_CHANGED",
    "MATERIAL_PREPARED", "MATERIAL_SKIPPED", "DOWNGRADE_SUGGESTED",
    "USER_DOWNGRADE_REQUESTED", "DOWNGRADE_CONFIRMED", "DOWNGRADE_DECLINED",
)
POLICY_VERSION = "1.0"
DOWNGRADE_TRIGGER_GATES = 2  # consecutive non-passed formal gates


class DifficultyHistoryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DifficultyHistoryService:
    def __init__(self, database: Database, now_fn=None) -> None:
        self.database = database
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    # ---------- event recording (idempotent) ----------

    def record(
        self,
        scope_id: str,
        event_type: str,
        *,
        stage_before: str | None = None,
        stage_after: str | None = None,
        source_record_ids: list[str] | None = None,
        actor: str = "SYSTEM",
        reason: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise DifficultyHistoryError("UNKNOWN_EVENT", f"unknown event type {event_type}")
        occurred_at = occurred_at or self.now_fn().isoformat()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM difficulty_events WHERE scope_id = ? AND event_type = ? AND occurred_at = ?",
                (scope_id, event_type, occurred_at),
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "reused": True}
            connection.execute(
                """
                INSERT INTO difficulty_events(
                    event_id, scope_id, event_type, occurred_at, stage_before, stage_after,
                    source_record_ids_json, actor, reason, policy_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), scope_id, event_type, occurred_at, stage_before, stage_after,
                    json.dumps(source_record_ids or []), actor, reason, POLICY_VERSION,
                    self.now_fn().isoformat(),
                ),
            )
        return self._get_by_key(scope_id, event_type, occurred_at)

    def _get_by_key(self, scope_id: str, event_type: str, occurred_at: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM difficulty_events WHERE scope_id = ? AND event_type = ? AND occurred_at = ?",
                (scope_id, event_type, occurred_at),
            ).fetchone()
        return dict(row)

    # ---------- history read ----------

    def history(self, scope_id: str) -> dict[str, Any]:
        self._materialize_cooldown_expiry(scope_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM difficulty_events WHERE scope_id = ? ORDER BY occurred_at ASC, rowid ASC",
                (scope_id,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["source_record_ids"] = json.loads(event.pop("source_record_ids_json"))
            events.append(event)
        return {
            "scope_id": scope_id,
            "policy_version": POLICY_VERSION,
            "stage_bands": ["VOA Slow", "接近正常语速", "正常语速"],
            "events": events,
            "materials": self._material_metadata(),
        }

    def _material_metadata(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT material_id, title, source_url, source_name, duration_seconds,
                       speech_rate_wpm, speed_stage, source_candidate_id
                  FROM materials
                 WHERE source_url IS NOT NULL OR source_candidate_id IS NOT NULL
                 ORDER BY created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _materialize_cooldown_expiry(self, scope_id: str) -> None:
        """Observe a lapsed cooldown as COOLDOWN_EXPIRED (idempotent)."""
        with self.database.connect() as connection:
            profile = connection.execute(
                "SELECT cooldown_until FROM training_difficulty_profiles WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if profile is None or profile["cooldown_until"] is None:
                return
            started = connection.execute(
                "SELECT occurred_at FROM difficulty_events WHERE scope_id = ? AND event_type = 'COOLDOWN_STARTED' ORDER BY occurred_at DESC LIMIT 1",
                (scope_id,),
            ).fetchone()
        if started is None:
            return
        if datetime.fromisoformat(profile["cooldown_until"]) <= self.now_fn():
            self.record(scope_id, "COOLDOWN_EXPIRED", source_record_ids=[started["occurred_at"]])

    # ---------- downgrade flow ----------

    def check_downgrade_suggestion(self, scope_id: str) -> dict[str, Any]:
        """System suggestion after two consecutive non-passed formal gates.
        Suggests only; never applies."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT gate_result FROM weekly_gate_records
                 WHERE scope_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (scope_id, DOWNGRADE_TRIGGER_GATES),
            ).fetchall()
        if len(rows) < DOWNGRADE_TRIGGER_GATES:
            return {"suggested": False, "reason": "insufficient_gate_history"}
        if not all(row["gate_result"] != "PASS" for row in rows):
            return {"suggested": False, "reason": "recent_gate_passed"}
        stage = self._current_stage(scope_id)
        if stage == "STAGE_1":
            return {"suggested": False, "reason": "already_minimum_stage"}
        event = self.record(
            scope_id, "DOWNGRADE_SUGGESTED", stage_before=stage, stage_after=stage,
            reason="two consecutive weekly gates not passed",
        )
        return {"suggested": True, "event": event}

    def downgrade_request(self, scope_id: str) -> dict[str, Any]:
        stage = self._current_stage(scope_id)
        if stage == "STAGE_1":
            raise DifficultyHistoryError("MIN_STAGE", "already at the minimum stage")
        return self.record(
            scope_id, "USER_DOWNGRADE_REQUESTED", stage_before=stage, stage_after=stage,
            actor="USER", reason="user requested downgrade",
        )

    def downgrade_confirm(self, scope_id: str) -> dict[str, Any]:
        """Explicit confirmation applies at most one Stage down and resets
        the formal eight-week counter. Never deletes prior history."""
        from app.core.difficulty_progression import STAGES

        stage = self._current_stage(scope_id)
        if stage == "STAGE_1":
            raise DifficultyHistoryError("MIN_STAGE", "already at the minimum stage")
        next_stage = STAGES[STAGES.index(stage) - 1]
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE training_difficulty_profiles
                   SET current_stage = ?, consecutive_pass_weeks = 0, upgrade_eligible = 0,
                       last_upgrade_decision = 'DOWNGRADE_CONFIRMED', last_upgrade_at = ?,
                       updated_at = ?
                 WHERE scope_id = ?
                """,
                (next_stage, self.now_fn().isoformat(), self.now_fn().isoformat(), scope_id),
            )
        return self.record(
            scope_id, "DOWNGRADE_CONFIRMED", stage_before=stage, stage_after=next_stage,
            actor="USER", reason="confirmed downgrade",
        )

    def downgrade_decline(self, scope_id: str) -> dict[str, Any]:
        stage = self._current_stage(scope_id)
        return self.record(
            scope_id, "DOWNGRADE_DECLINED", stage_before=stage, stage_after=stage,
            actor="USER", reason="user declined downgrade suggestion",
        )

    def _current_stage(self, scope_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT current_stage FROM training_difficulty_profiles WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
        if row is None:
            return "STAGE_1"
        return row["current_stage"]
