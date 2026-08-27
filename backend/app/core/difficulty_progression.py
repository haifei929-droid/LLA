"""P1 difficulty progression (3.4/3.5): 8-week stability, prompts, cooldown,
single-variable upgrade, Stage 3 cap.

P1 only reads P0 weekly-assessment results; it never modifies P0 records.
WeeklyGateRecord writes are idempotent per (scope, training_week, stage).
Consecutive counting follows the training-week sequence: a PASS week with
dictation >= 80 and (if attempted) a passed read-aloud advances the counter;
anything else resets it, and a gap between week records resets it too.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.connection import Database

STAGES = ["STAGE_1", "STAGE_2", "STAGE_3"]
STABLE_WEEKS_REQUIRED = 8
COOLDOWN_DAYS = 28  # 4 training weeks

GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_REINFORCEMENT = "REINFORCEMENT_REQUIRED"
GATE_INCOMPLETE = "INCOMPLETE"


def _absolute_week_index(value: datetime) -> int:
    """Calendar-week ordinal used for week-sequence continuity checks."""
    return (value.date() - datetime(1970, 1, 1, tzinfo=UTC).date()).days // 7


class DifficultyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DifficultyProgressionService:
    def __init__(
        self,
        database: Database,
        weekly_assessments,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.weekly_assessments = weekly_assessments
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    # ---------- profile ----------

    def get_profile(self, scope_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM training_difficulty_profiles WHERE scope_id = ?", (scope_id,)
            ).fetchone()
        if row is None:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO training_difficulty_profiles(scope_id, current_stage, updated_at)
                    VALUES (?, 'STAGE_1', ?)
                    """,
                    (scope_id, self.now_fn().isoformat()),
                )
            return self.get_profile(scope_id)
        profile = dict(row)
        # Eligibility is derived, not stored truth: a lapsed cooldown re-arms
        # the prompt without a write.
        profile["upgrade_eligible"] = (
            profile["consecutive_pass_weeks"] >= STABLE_WEEKS_REQUIRED
            and profile["current_stage"] != "STAGE_3"
            and (
                profile["cooldown_until"] is None
                or datetime.fromisoformat(profile["cooldown_until"]) <= self.now_fn()
            )
        )
        return profile

    def _update_profile(self, scope_id: str, **fields) -> dict[str, object]:
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            values = list(fields.values())
        else:
            assignments, values = "1 = 1", []
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE training_difficulty_profiles SET {assignments}, updated_at = ? WHERE scope_id = ?",
                [*values, self.now_fn().isoformat(), scope_id],
            )
        return self.get_profile(scope_id)

    # ---------- weekly gate ----------

    def evaluate_weekly_gate(self, scope_id: str, training_week_id: str) -> dict[str, object]:
        """Read the P0 weekly assessment and record an idempotent gate row.

        Idempotency invariant (P1 8): a repeated evaluation of the same
        training week at the same stage returns the existing record — and
        always recomputes the streak, so a lost response followed by a retry
        can never leave the profile inconsistent with the records. A racing
        duplicate insert (UNIQUE violation) resolves to the existing row too.
        """
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM weekly_gate_records
                 WHERE scope_id = ? AND training_week_id = ? AND stage_at_evaluation = (
                     SELECT current_stage FROM training_difficulty_profiles WHERE scope_id = ?
                 )
                """,
                (scope_id, training_week_id, scope_id),
            ).fetchone()
        if existing is not None:
            # Always recompute so a lost-response retry can never leave the
            # profile inconsistent with the records.
            self._recompute_consecutive(scope_id)
            return {"record": dict(existing), "reused": True}

        assessment = self.weekly_assessments.get(training_week_id)
        stage = self.get_profile(scope_id)["current_stage"]
        result, score, read_score, read_attempted, reasons = self._map_assessment(assessment)

        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO weekly_gate_records(
                        gate_id, scope_id, training_week_id, stage_at_evaluation, gate_result,
                        dictation_score, read_aloud_score, read_aloud_attempted,
                        evaluation_reason_codes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()), scope_id, training_week_id, stage, result, score,
                        read_score, int(read_attempted), json.dumps(reasons), self.now_fn().isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            # A concurrent evaluation of the same week+stage already landed;
            # resolve to the existing row instead of failing.
            with self.database.connect() as connection:
                existing = connection.execute(
                    """
                    SELECT * FROM weekly_gate_records
                     WHERE scope_id = ? AND training_week_id = ? AND stage_at_evaluation = ?
                    """,
                    (scope_id, training_week_id, stage),
                ).fetchone()
            self._recompute_consecutive(scope_id)
            return {"record": dict(existing), "reused": True}

        self._recompute_consecutive(scope_id)
        return {"record": self._latest_record(scope_id), "reused": False}

    def _map_assessment(self, assessment: dict[str, object]) -> tuple[str, float | None, str | None, bool, list[str]]:
        reasons: list[str] = []
        gate_status = assessment["gate_status"]
        score = assessment.get("dictation_score")
        if gate_status == "WEEKLY_GATE_PASS":
            result = GATE_PASS
        elif gate_status == "REINFORCEMENT_REQUIRED":
            result = GATE_REINFORCEMENT
            reasons.append("gate_reinforcement_required")
        else:
            result = GATE_INCOMPLETE
            reasons.append("gate_incomplete")
        if score is None:
            reasons.append("dictation_not_scored")
        elif score < 80:
            result = GATE_FAIL
            reasons.append("dictation_below_80")
        read_attempted = bool(assessment.get("reading_required"))
        read_score = None
        if read_attempted:
            dimensions = assessment.get("reading_dimension_results") or {}
            read_score = "PASS" if dimensions and all(dimensions.values()) else "FAIL"
            if read_score == "FAIL":
                reasons.append("reading_failed")
                if result == GATE_PASS:
                    result = GATE_FAIL
        return result, score, read_score, read_attempted, reasons

    def _latest_record(self, scope_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM weekly_gate_records WHERE scope_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (scope_id,),
            ).fetchone()
        return dict(row) if row else None

    def _recompute_consecutive(self, scope_id: str) -> None:
        """Recompute the streak from the records of the CURRENT stage only.

        Invariants (P1 3.4/3.5):
        - cross-stage records never contribute (an upgrade resets the count);
        - a missing week (a gap of more than one calendar week between
          records) breaks the sequence and resets the count;
        - a non-passing week resets the count.
        """
        stage = self.get_profile(scope_id)["current_stage"]
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM weekly_gate_records WHERE scope_id = ? AND stage_at_evaluation = ? ORDER BY created_at ASC, rowid ASC",
                (scope_id, stage),
            ).fetchall()
        consecutive = 0
        previous_week_index: int | None = None
        for row in rows:
            week_index = _absolute_week_index(datetime.fromisoformat(row["created_at"]))
            if previous_week_index is not None and week_index - previous_week_index > 1:
                # A calendar week with no record breaks the sequence.
                consecutive = 0
            passes = (
                row["gate_result"] == GATE_PASS
                and (row["dictation_score"] is None or row["dictation_score"] >= 80)
                and (not row["read_aloud_attempted"] or row["read_aloud_score"] == "PASS")
            )
            consecutive = consecutive + 1 if passes else 0
            previous_week_index = week_index
        profile = self.get_profile(scope_id)
        eligible = (
            consecutive >= STABLE_WEEKS_REQUIRED
            and profile["current_stage"] != "STAGE_3"
            and (profile["cooldown_until"] is None or datetime.fromisoformat(profile["cooldown_until"]) <= self.now_fn())
        )
        self._update_profile(
            scope_id,
            consecutive_pass_weeks=consecutive,
            upgrade_eligible=int(eligible),
        )

    # ---------- prompt & decision ----------

    def current_prompt(self, scope_id: str) -> dict[str, object] | None:
        """Return the pending prompt for the current stage, if any."""
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM upgrade_prompts
                 WHERE scope_id = ? AND prompt_status = 'PENDING'
                 ORDER BY created_at DESC LIMIT 1
                """,
                (scope_id,),
            ).fetchone()
        return dict(row) if row else None

    def ensure_prompt(self, scope_id: str) -> dict[str, object]:
        """Generate the upgrade prompt when eligible; idempotent per stage."""
        profile = self.get_profile(scope_id)
        if profile["current_stage"] == "STAGE_3":
            raise DifficultyError("MAX_STAGE_REACHED", "already at the highest stage")
        pending = self.current_prompt(scope_id)
        if pending is not None and pending["stage_at_prompt"] == profile["current_stage"]:
            return pending
        if not profile["upgrade_eligible"]:
            raise DifficultyError(
                "NOT_ELIGIBLE",
                f"eligible requires {STABLE_WEEKS_REQUIRED} consecutive passes; current {profile['consecutive_pass_weeks']}",
            )
        prompt_id = str(uuid4())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO upgrade_prompts(prompt_id, scope_id, stage_at_prompt, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (prompt_id, scope_id, profile["current_stage"], self.now_fn().isoformat()),
            )
            connection.execute(
                "UPDATE training_difficulty_profiles SET last_upgrade_prompt_at = ? WHERE scope_id = ?",
                (self.now_fn().isoformat(), scope_id),
            )
        return self.current_prompt(scope_id)

    def decide_upgrade(
        self, scope_id: str, prompt_id: str, decision: str, idempotency_key: str
    ) -> dict[str, object]:
        if decision not in ("UPGRADE_CONFIRMED", "KEEP_CURRENT", "DECIDE_LATER"):
            raise DifficultyError("INVALID_DECISION", f"unknown decision {decision}")
        with self.database.connect() as connection:
            prompt = connection.execute(
                "SELECT * FROM upgrade_prompts WHERE prompt_id = ? AND scope_id = ?",
                (prompt_id, scope_id),
            ).fetchone()
        if prompt is None:
            raise DifficultyError("PROMPT_NOT_FOUND", "no such prompt for this scope")
        if prompt["prompt_status"] != "PENDING":
            return {
                "prompt_id": prompt_id,
                "decision": prompt["decision"],
                "reused": True,
                "profile": self.get_profile(scope_id),
            }
        profile = self.get_profile(scope_id)
        if prompt["stage_at_prompt"] != profile["current_stage"]:
            raise DifficultyError("PROMPT_STAGE_MISMATCH", "prompt belongs to another stage")

        now = self.now_fn()
        with self.database.connect() as connection:
            if decision == "UPGRADE_CONFIRMED":
                if profile["current_stage"] == "STAGE_3":
                    raise DifficultyError("MAX_STAGE_REACHED", "already at the highest stage")
                next_stage = STAGES[STAGES.index(profile["current_stage"]) + 1]
                connection.execute(
                    """
                    UPDATE training_difficulty_profiles
                       SET current_stage = ?, consecutive_pass_weeks = 0, upgrade_eligible = 0,
                           last_upgrade_decision = ?, last_upgrade_at = ?, cooldown_until = NULL,
                           profile_version = printf('%.1f', CAST(profile_version AS REAL) + 0.1),
                           updated_at = ?
                     WHERE scope_id = ?
                    """,
                    (next_stage, decision, now.isoformat(), now.isoformat(), scope_id),
                )
            else:
                cooldown_until = now + timedelta(days=COOLDOWN_DAYS)
                connection.execute(
                    """
                    UPDATE training_difficulty_profiles
                       SET last_upgrade_decision = ?, cooldown_until = ?, upgrade_eligible = 0, updated_at = ?
                     WHERE scope_id = ?
                    """,
                    (decision, cooldown_until.isoformat(), now.isoformat(), scope_id),
                )
            connection.execute(
                """
                UPDATE upgrade_prompts
                   SET prompt_status = 'RESOLVED', decision = ?, idempotency_key = ?, resolved_at = ?
                 WHERE prompt_id = ?
                """,
                (decision, idempotency_key, now.isoformat(), prompt_id),
            )
        return {
            "prompt_id": prompt_id,
            "decision": decision,
            "reused": False,
            "profile": self.get_profile(scope_id),
        }
