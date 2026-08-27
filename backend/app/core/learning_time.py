from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from app.core.time_window import week_window_start
from app.db.connection import Database

#: Legal activity types per Spec 13.1; the only caller today (the time-logs
#: API and existing tests) sends "DICTATION", which is in the set.
LEGAL_ACTIVITY_TYPES = frozenset(
    {
        "FIRST_FULL_LISTEN",
        "DICTATION",
        "SECOND_FULL_LISTEN",
        "READING",
        "FULL_READING_ASSESSMENT",
        "WEEKLY_TEST",
        "REINFORCEMENT",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LearningTimeService:
    """Persist active learning intervals and aggregate their duration."""

    def __init__(
        self,
        database: Database,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.now_fn = now_fn or _utc_now
        self.weekly_window = self.database.settings.weekly_window
        if self.weekly_window not in {"calendar", "rolling7"}:
            raise ValueError(f"Unknown weekly_window: {self.weekly_window}")

    def start(
        self, *, activity_type: str, material_id: str | None = None, session_id: str | None = None
    ) -> dict[str, object]:
        activity_type = activity_type.strip()
        if not activity_type:
            raise ValueError("activity_type must not be empty")
        if activity_type not in LEGAL_ACTIVITY_TYPES:
            raise ValueError(f"Unknown activity_type: {activity_type}")
        with self.database.connect() as connection:
            if material_id is not None:
                material = connection.execute(
                    "SELECT material_id FROM materials WHERE material_id = ?", (material_id,)
                ).fetchone()
                if material is None:
                    raise KeyError(f"No material exists for {material_id}")
            row = {
                "time_log_id": str(uuid4()),
                "start_time": self.now_fn().isoformat(),
                "end_time": None,
                "active_seconds": 0,
                "activity_type": activity_type,
                "material_id": material_id,
                "session_id": session_id,
            }
            connection.execute(
                """
                INSERT INTO training_time_logs(
                    time_log_id, start_time, active_seconds, activity_type, material_id, session_id
                ) VALUES (?, ?, 0, ?, ?, ?)
                """,
                (
                    row["time_log_id"],
                    row["start_time"],
                    row["activity_type"],
                    material_id,
                    session_id,
                ),
            )
        return row

    def stop(self, time_log_id: str, active_seconds: int) -> dict[str, object]:
        if active_seconds < 0:
            raise ValueError("active_seconds must be non-negative")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM training_time_logs WHERE time_log_id = ?", (time_log_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No time log exists for {time_log_id}")
            if row["end_time"] is not None:
                raise ValueError("Time log has already been stopped")
            end_time = self.now_fn().isoformat()
            connection.execute(
                "UPDATE training_time_logs SET end_time = ?, active_seconds = ? WHERE time_log_id = ?",
                (end_time, active_seconds, time_log_id),
            )
            # All three columns stay monotonic accumulators; the weekly column
            # is kept for schema consistency but stats() overrides it with the
            # window aggregate, which is the authoritative weekly value.
            connection.execute(
                """
                UPDATE learning_stats
                   SET session_learning_seconds = session_learning_seconds + ?,
                       weekly_learning_seconds = weekly_learning_seconds + ?,
                       total_learning_seconds = total_learning_seconds + ?
                 WHERE stats_id = 1
                """,
                (active_seconds, active_seconds, active_seconds),
            )
            result = dict(row)
            result.update(end_time=end_time, active_seconds=active_seconds)
        return result

    def stats(self) -> dict[str, object]:
        now = self.now_fn()
        window_start = week_window_start(now, self.weekly_window)
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM learning_stats WHERE stats_id = 1").fetchone()
            if row is None:
                raise KeyError("Learning stats have not been initialized")
            logs = connection.execute(
                "SELECT start_time, active_seconds FROM training_time_logs WHERE end_time IS NOT NULL"
            ).fetchall()
        # start_time is an ISO string; compare in Python rather than via SQL so
        # the boundary check is correct regardless of stored microsecond width.
        weekly_seconds = sum(
            log["active_seconds"]
            for log in logs
            if datetime.fromisoformat(log["start_time"]) >= window_start
        )
        result = dict(row)
        result["weekly_learning_seconds"] = weekly_seconds
        return result
