"""Weekly assessment loop: auto-required tests, 80% dictation gate,
short reinforcement package, and targeted retest (Spec 14/15).

The service owns the weekly gate rules; the training state machine (material
level) and the weekly gate stay independent as required by the spec. Test-item
and reinforcement generation are rule-based minimal implementations: sentences
are drawn deterministically from the material pool (seeded by week_id) and
never introduce vocabulary outside it.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import Settings
from app.core.dictation import evaluate_dictation
from app.core.states import WeeklyState
from app.core.time_window import week_window_start
from app.db.connection import Database

_READING_ACTIVITIES = {"READING", "FULL_READING_ASSESSMENT"}
_LISTENING_ACTIVITIES = {"DICTATION", "FIRST_FULL_LISTEN", "SECOND_FULL_LISTEN"}


class WeeklyAssessmentService:
    """Deterministic weekly gate for dictation and reading requirements."""

    def __init__(self, database: Database, settings: Settings, now_fn: Callable[[], datetime] | None = None) -> None:
        self.database = database
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        week_id: str,
        period_start: str,
        period_end: str,
        dictation_required: bool | None = None,
        reading_required: bool | None = None,
    ) -> dict[str, object]:
        if not week_id.strip():
            raise ValueError("week_id is required")
        if not period_start.strip() or not period_end.strip():
            # Default the period to the current weekly window when omitted.
            window_start = week_window_start(self.now_fn(), self.settings.weekly_window)
            window_end = week_window_start(
                self.now_fn() + timedelta(days=7), self.settings.weekly_window
            )
            period_start = period_start or window_start.date().isoformat()
            period_end = period_end or window_end.date().isoformat()
        if period_end < period_start:
            raise ValueError("period_end must not be before period_start")
        if dictation_required is None or reading_required is None:
            inferred = self._infer_requirements()
            if dictation_required is None:
                dictation_required = inferred["dictation"]
            if reading_required is None:
                reading_required = inferred["reading"]
        with self.database.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO weekly_assessments(
                        week_id, period_start, period_end, dictation_required, reading_required,
                        gate_status, reinforcement_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'NOT_REQUIRED', datetime('now'))
                    """,
                    (
                        week_id,
                        period_start,
                        period_end,
                        int(dictation_required),
                        int(reading_required),
                        WeeklyState.WEEKLY_ASSESSMENT_READY.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Weekly assessment already exists for {week_id}") from exc
        return self.get(week_id)

    def _infer_requirements(self) -> dict[str, bool]:
        """Spec 14.1: test type follows what the user actually trained this week."""
        window_start = week_window_start(self.now_fn(), self.settings.weekly_window)
        with self.database.connect() as connection:
            logs = connection.execute(
                "SELECT start_time, activity_type FROM training_time_logs WHERE end_time IS NOT NULL"
            ).fetchall()
        # Filter by start_time in Python for the same reason as time stats.
        trained = {
            row["activity_type"]
            for row in logs
            if datetime.fromisoformat(row["start_time"]) >= window_start
        }
        return {
            "dictation": bool(trained & _LISTENING_ACTIVITIES),
            "reading": bool(trained & _READING_ACTIVITIES),
        }

    def record_dictation(self, week_id: str, score: float, passed: bool) -> dict[str, object]:
        if not 0 <= score <= 100:
            raise ValueError("dictation score must be between 0 and 100")
        self._ensure_exists(week_id)
        # Spec 14.2: below the threshold the gate must not recommend the next
        # round, regardless of what the caller reports.
        if score < self.settings.weekly_dictation_pass_threshold:
            passed = False
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE weekly_assessments SET dictation_score = ?, dictation_pass = ? WHERE week_id = ?",
                (score, int(passed), week_id),
            )
        return self.evaluate(week_id)

    def record_reading(self, week_id: str, dimensions: dict[str, bool]) -> dict[str, object]:
        if not dimensions:
            raise ValueError("reading dimensions must not be empty")
        self._ensure_exists(week_id)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE weekly_assessments SET reading_dimension_results = ? WHERE week_id = ?",
                (json.dumps(dimensions, sort_keys=True), week_id),
            )
        return self.evaluate(week_id)

    def evaluate(self, week_id: str) -> dict[str, object]:
        assessment = self.get(week_id)
        dictation_ok = not assessment["dictation_required"] or assessment["dictation_pass"] is True
        dimensions = assessment["reading_dimension_results"]
        reading_ok = not assessment["reading_required"] or bool(dimensions) and all(dimensions.values())
        passed = dictation_ok and reading_ok
        gate_status = WeeklyState.WEEKLY_GATE_PASS.value if passed else WeeklyState.REINFORCEMENT_REQUIRED.value
        reinforcement_status = "NOT_REQUIRED" if passed else WeeklyState.REINFORCEMENT_REQUIRED.value
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE weekly_assessments SET gate_status = ?, reinforcement_status = ? WHERE week_id = ?",
                (gate_status, reinforcement_status, week_id),
            )
        return self.get(week_id)

    def create_test_items(self, week_id: str, count: int | None = None) -> list[dict[str, object]]:
        """Generate the dictation test: sentences drawn from the material pool.

        Spec 14.2: the test never introduces vocabulary outside the week's
        material. Generation is deterministic per week_id (seeded), and the
        count is configurable (item-count details are a pending-calibration
        item).
        """
        self._ensure_exists(week_id)
        count = count or self.settings.weekly_test_sentence_count
        if count < 1:
            raise ValueError("count must be at least 1")
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) AS n FROM weekly_test_items WHERE week_id = ? AND kind = 'TEST'",
                (week_id,),
            ).fetchone()["n"]
            if existing:
                return self.list_test_items(week_id, kind="TEST")
            pool = connection.execute(
                "SELECT sentence_id, text FROM sentences ORDER BY material_id, sequence_no"
            ).fetchall()
        if not pool:
            raise ValueError("No sentences available; import material before generating the weekly test")
        rng = random.Random(f"weekly:{week_id}")
        selected = rng.sample(list(pool), min(count, len(pool)))
        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO weekly_test_items(item_id, week_id, kind, sentence_id, text, created_at)
                VALUES (?, ?, 'TEST', ?, ?, datetime('now'))
                """,
                [
                    (str(uuid4()), week_id, row["sentence_id"], row["text"])
                    for row in selected
                ],
            )
        return self.list_test_items(week_id, kind="TEST")

    def submit_test_dictation(self, week_id: str, item_id: str, user_text: str, listen_count: int) -> dict[str, object]:
        if listen_count < 1:
            raise ValueError("listen_count must be at least 1")
        return self._submit_item(week_id, item_id, user_text, listen_count, kind="TEST")

    def start_reinforcement(self, week_id: str) -> dict[str, object]:
        """Spec 15.1: build a short package from this week's weak targets.

        Sources, in priority order: weekly-test items that failed the exact
        match (they carry this week's error words), non-SPELLING errors in the
        week's training dictation attempts, and Listening Memory entries that
        still need several passes or a reveal. Sentences are drawn from the
        material pool so no out-of-scope vocabulary is introduced.
        """
        assessment = self.get(week_id)
        if assessment["gate_status"] != WeeklyState.REINFORCEMENT_REQUIRED.value:
            raise ValueError("Reinforcement is only available after a failed weekly gate")
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) AS n FROM weekly_test_items WHERE week_id = ? AND kind = 'REINFORCEMENT'",
                (week_id,),
            ).fetchone()["n"]
            if existing:
                return self._with_reinforcement(self.get(week_id))

            window_start = week_window_start(self.now_fn(), self.settings.weekly_window)
            failed_tests = connection.execute(
                """
                SELECT item_id, sentence_id, text FROM weekly_test_items
                 WHERE week_id = ? AND kind = 'TEST' AND is_exact = 0
                """,
                (week_id,),
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT a.error_details, a.sentence_id
                  FROM dictation_attempts a
                  JOIN sentences s ON s.sentence_id = a.sentence_id
                 WHERE a.created_at >= ?
                """,
                (window_start.isoformat(),),
            ).fetchall()
            memory = connection.execute(
                "SELECT target, avg_attempt_before_correct, reveal_count, last_seen_at FROM listening_memory"
            ).fetchall()
            pool = connection.execute(
                "SELECT sentence_id, text FROM sentences ORDER BY material_id, sequence_no"
            ).fetchall()

        weak_targets: set[str] = set()
        for attempt in attempts:
            for error in json.loads(attempt["error_details"]):
                if error.get("expected") and error.get("error_type") != "SPELLING":
                    weak_targets.add(error["expected"].lower())
        for row in memory:
            # last_seen_at may be a naive SQLite timestamp; compare in UTC.
            last_seen = datetime.fromisoformat(row["last_seen_at"])
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            if last_seen >= window_start and (
                (row["avg_attempt_before_correct"] or 0) >= 3 or (row["reveal_count"] or 0) > 0
            ):
                weak_targets.add(row["target"].lower())

        selected: list[dict[str, object]] = []
        used_ids: set[str] = set()
        # 1) This week's failed test sentences are the direct targets.
        for row in failed_tests:
            if row["sentence_id"] not in used_ids:
                selected.append({"sentence_id": row["sentence_id"], "text": row["text"]})
                used_ids.add(row["sentence_id"])
        # 2) Match remaining weak targets against the material pool.
        for target in weak_targets:
            for sentence in pool:
                if target in sentence["text"].lower() and sentence["sentence_id"] not in used_ids:
                    selected.append({"sentence_id": sentence["sentence_id"], "text": sentence["text"]})
                    used_ids.add(sentence["sentence_id"])
                    break
        limit = self.settings.reinforcement_max_sentences
        selected = selected[:limit]
        if not selected:
            raise ValueError(
                "No reinforcement sentences could be generated from this week's weak targets"
            )
        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO weekly_test_items(item_id, week_id, kind, sentence_id, text, created_at)
                VALUES (?, ?, 'REINFORCEMENT', ?, ?, datetime('now'))
                """,
                [(str(uuid4()), week_id, row["sentence_id"], row["text"]) for row in selected],
            )
            connection.execute(
                "UPDATE weekly_assessments SET gate_status = ?, reinforcement_status = ? WHERE week_id = ?",
                (WeeklyState.REINFORCEMENT.value, WeeklyState.REINFORCEMENT.value, week_id),
            )
        return self._with_reinforcement(self.get(week_id))

    def submit_reinforcement_dictation(self, week_id: str, item_id: str, user_text: str, listen_count: int) -> dict[str, object]:
        if listen_count < 1:
            raise ValueError("listen_count must be at least 1")
        result = self._submit_item(week_id, item_id, user_text, listen_count, kind="REINFORCEMENT")
        # Spec 15.3: once every reinforcement item is exact, the targeted
        # retest is armed; an explicit confirm completes it and the gate
        # recovers to WEEKLY_GATE_PASS.
        items = self.list_test_items(week_id, kind="REINFORCEMENT")
        if items and all(item["is_exact"] for item in items):
            with self.database.connect() as connection:
                connection.execute(
                    """
                    UPDATE weekly_assessments
                       SET gate_status = ?, reinforcement_status = 'PENDING_RETEST'
                     WHERE week_id = ?
                    """,
                    (WeeklyState.TARGETED_RETEST.value, week_id),
                )
        result["assessment"] = self._with_reinforcement(self.get(week_id))
        return result

    def confirm_retest(self, week_id: str) -> dict[str, object]:
        """Explicit confirmation of the targeted retest (Spec 15.3): the gate
        recovers to WEEKLY_GATE_PASS only after the retest is confirmed."""
        assessment = self.get(week_id)
        if assessment["gate_status"] != WeeklyState.TARGETED_RETEST.value:
            raise ValueError("Targeted retest is only available after the reinforcement package is complete")
        items = self.list_test_items(week_id, kind="REINFORCEMENT")
        if not items or not all(item["is_exact"] for item in items):
            raise ValueError("Every reinforcement item must be exact before the retest passes")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE weekly_assessments
                   SET gate_status = ?, reinforcement_status = 'COMPLETED'
                 WHERE week_id = ?
                """,
                (WeeklyState.WEEKLY_GATE_PASS.value, week_id),
            )
        return self.get(week_id)

    def _submit_item(self, week_id: str, item_id: str, user_text: str, listen_count: int, *, kind: str) -> dict[str, object]:
        self._ensure_exists(week_id)
        with self.database.connect() as connection:
            item = connection.execute(
                "SELECT * FROM weekly_test_items WHERE week_id = ? AND item_id = ? AND kind = ?",
                (week_id, item_id, kind),
            ).fetchone()
            if item is None:
                raise KeyError(f"No {kind.lower()} item exists for {item_id}")
            if item["is_exact"]:
                raise ValueError("This item has already been completed exactly")
            result = evaluate_dictation(item["text"], user_text)
            connection.execute(
                """
                UPDATE weekly_test_items
                   SET is_exact = ?, attempt_count = attempt_count + 1
                 WHERE item_id = ?
                """,
                (int(result.is_exact_match), item_id),
            )
            self._maybe_finish_test(connection, week_id, kind)
        return {
            "item_id": item_id,
            "is_exact_match": result.is_exact_match,
            "errors": [json.loads(json.dumps(error.__dict__)) for error in result.errors],
            "attempt_count": int(item["attempt_count"]) + 1,
        }

    def _maybe_finish_test(self, connection, week_id: str, kind: str) -> None:
        """Score the weekly dictation once every TEST item has been attempted."""
        if kind != "TEST":
            return
        summary = connection.execute(
            """
            SELECT COUNT(*) AS total, SUM(CASE WHEN attempt_count > 0 THEN 1 ELSE 0 END) AS attempted,
                   SUM(is_exact) AS exact
              FROM weekly_test_items WHERE week_id = ? AND kind = 'TEST'
            """,
            (week_id,),
        ).fetchone()
        if summary["total"] == 0 or summary["attempted"] != summary["total"]:
            return
        score = round((summary["exact"] or 0) / summary["total"] * 100, 1)
        passed = score >= self.settings.weekly_dictation_pass_threshold
        assessment = connection.execute(
            """
            SELECT dictation_required, reading_required, reading_dimension_results
              FROM weekly_assessments WHERE week_id = ?
            """,
            (week_id,),
        ).fetchone()
        dimensions = json.loads(assessment["reading_dimension_results"])
        dictation_ok = not assessment["dictation_required"] or passed
        reading_ok = not assessment["reading_required"] or bool(dimensions) and all(dimensions.values())
        gate_passed = dictation_ok and reading_ok
        connection.execute(
            """
            UPDATE weekly_assessments
               SET dictation_score = ?, dictation_pass = ?, gate_status = ?, reinforcement_status = ?
             WHERE week_id = ?
            """,
            (
                score,
                int(passed),
                WeeklyState.WEEKLY_GATE_PASS.value if gate_passed else WeeklyState.REINFORCEMENT_REQUIRED.value,
                "NOT_REQUIRED" if gate_passed else WeeklyState.REINFORCEMENT_REQUIRED.value,
                week_id,
            ),
        )

    def list_test_items(self, week_id: str, kind: str = "TEST") -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.item_id, i.kind, i.sentence_id, i.text, i.is_exact, i.attempt_count,
                       s.start_time, s.end_time
                  FROM weekly_test_items i
                  LEFT JOIN sentences s ON s.sentence_id = i.sentence_id
                 WHERE i.week_id = ? AND i.kind = ?
                 ORDER BY i.created_at, i.item_id
                """,
                (week_id, kind),
            ).fetchall()
        return [dict(row) for row in rows]

    def list(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM weekly_assessments ORDER BY created_at DESC"
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get(self, week_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM weekly_assessments WHERE week_id = ?", (week_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"No weekly assessment exists for {week_id}")
        return self._decode(row)

    @staticmethod
    def _decode(row) -> dict[str, object]:
        result = dict(row)
        result["dictation_required"] = bool(result["dictation_required"])
        result["reading_required"] = bool(result["reading_required"])
        result["dictation_pass"] = (
            None if result["dictation_pass"] is None else bool(result["dictation_pass"])
        )
        result["reading_dimension_results"] = json.loads(result["reading_dimension_results"])
        return result

    def _with_reinforcement(self, assessment: dict[str, object]) -> dict[str, object]:
        assessment["reinforcement_items"] = self.list_test_items(
            assessment["week_id"], kind="REINFORCEMENT"
        )
        return assessment

    def _ensure_exists(self, week_id: str) -> None:
        self.get(week_id)
