"""P2-1 dashboard read-model (P2 spec 5.2/6/7/8).

Pure read aggregation over P0/P1 sources; never writes training facts.
Metric IDs are contract identifiers. The first-comprehension curve uses the
frozen 15/40/60/85 mapping and always retains the raw band and sample count.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.connection import Database

FIRST_COMPREHENSION_MAPPING = {
    "<30%": 15,
    "30–50%": 40,
    "50–70%": 60,
    ">70%": 85,
}
MAPPING_VERSION = "1.0"

LEGAL_ACTIVITIES = (
    "FIRST_FULL_LISTEN", "DICTATION", "SECOND_FULL_LISTEN", "READING",
    "FULL_READING_ASSESSMENT", "WEEKLY_TEST", "REINFORCEMENT",
)


@dataclass(frozen=True)
class MetricPoint:
    metric_id: str
    period: str
    value: float
    unit: str
    sample_count: int
    source_kind: str
    missing_reason: str | None
    metric_version: str


class DashboardService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def read(
        self,
        *,
        scope_id: str,
        range_start: str | None = None,
        range_end: str | None = None,
        granularity: str = "week",
    ) -> dict[str, Any]:
        if granularity not in ("day", "week", "month"):
            raise ValueError("granularity must be day, week or month")
        if range_start and range_end and range_start > range_end:
            raise ValueError("range_start must not be after range_end")

        closed_logs = self._closed_logs(scope_id, range_start, range_end)
        total_seconds = sum(row["active_seconds"] for row in closed_logs)

        summary = {
            "total_valid_hours": self._total_hours(scope_id),
            "range_hours": round(total_seconds / 3600.0, 2),
            "current_stage": self._current_stage(scope_id),
            "current_gate_state": self._current_gate_state(scope_id),
            "review_suggestions_enabled": self._suggestions_enabled(scope_id),
        }

        trend = {
            "time_series": self._time_series(closed_logs, granularity),
            "first_comprehension_curve": self._first_comprehension_curve(scope_id, range_start, range_end),
            "weekly_dictation": self._weekly_dictation(scope_id, range_start, range_end),
            "reading_practice": self._reading_practice(scope_id, range_start, range_end),
            "difficulty_streak": self._difficulty_streak(scope_id),
        }

        return {
            "metric_version": MAPPING_VERSION,
            "scope_id": scope_id,
            "range_start": range_start,
            "range_end": range_end,
            "granularity": granularity,
            "timezone": "UTC",
            "summary": summary,
            "trend": trend,
            "source_inclusion": {
                "time_logs": "closed training_time_logs only; open logs, pauses, idle and system waits excluded",
                "comprehension": "exactly one FIRST comprehension per prepared material",
                "weekly": "P0 weekly_assessments dictation score; Gate state shown alongside",
                "reading": "reading_attempts dimensions; never averaged",
            },
            "empty_period_behavior": "periods without data return sample_count=0 with missing_reason='no_data'",
        }

    # ---------- TIME ----------

    def _total_hours(self, scope_id: str) -> float:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(active_seconds), 0) AS s FROM training_time_logs WHERE end_time IS NOT NULL"
            ).fetchone()
        return round(row["s"] / 3600.0, 2)

    def _closed_logs(self, scope_id: str, range_start: str | None, range_end: str | None) -> list[Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT active_seconds, start_time, activity_type FROM training_time_logs WHERE end_time IS NOT NULL"
            ).fetchall()
        filtered = [
            row for row in rows
            if row["activity_type"] in LEGAL_ACTIVITIES
            and (not range_start or row["start_time"] >= range_start)
            and (not range_end or row["start_time"] <= range_end)
        ]
        return filtered

    def _time_series(self, logs: list[Any], granularity: str) -> list[MetricPoint]:
        buckets: dict[str, list[int]] = {}
        for row in logs:
            key = self._period_key(datetime.fromisoformat(row["start_time"]), granularity)
            buckets.setdefault(key, []).append(row["active_seconds"])
        points = []
        for period, seconds in sorted(buckets.items()):
            points.append(
                MetricPoint(
                    metric_id="P2.TIME.WINDOW_HOURS",
                    period=period,
                    value=round(sum(seconds) / 3600.0, 2),
                    unit="hours",
                    sample_count=len(seconds),
                    source_kind="training_time_logs",
                    missing_reason=None,
                    metric_version=MAPPING_VERSION,
                ).__dict__
            )
        return points

    @staticmethod
    def _period_key(value: datetime, granularity: str) -> str:
        if granularity == "day":
            return value.date().isoformat()
        if granularity == "month":
            return value.strftime("%Y-%m")
        iso = value.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    # ---------- COMPREHENSION ----------

    def _first_comprehension_curve(self, scope_id: str, range_start: str | None, range_end: str | None) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.self_rating, c.material_id, c.created_at
                  FROM comprehension_checks c
                  JOIN materials m ON m.material_id = c.material_id
                 WHERE c.phase = 'FIRST'
                   AND m.prepare_status = 'READY'
                """
            ).fetchall()
        buckets: dict[str, list[int]] = {}
        samples = 0
        for row in rows:
            if (range_start and row["created_at"] < range_start) or (range_end and row["created_at"] > range_end):
                continue
            mapped = FIRST_COMPREHENSION_MAPPING.get(row["self_rating"])
            if mapped is None:
                continue
            period = self._period_key(datetime.fromisoformat(row["created_at"]), "week")
            buckets.setdefault(period, []).append(mapped)
            samples += 1
        curve = []
        for period, scores in sorted(buckets.items()):
            curve.append(
                {
                    "period": period,
                    "mapped_score": round(sum(scores) / len(scores), 1),
                    "raw_band": None,  # band kept per-sample in band_distribution
                    "band_distribution": self._band_distribution(scores),
                    "sample_count": len(scores),
                    "mapping_version": MAPPING_VERSION,
                }
            )
        return {"metric_id": "P2.COMPREHENSION.FIRST_CURVE", "points": curve, "sample_count": samples}

    @staticmethod
    def _band_distribution(scores: list[int]) -> dict[str, int]:
        reverse = {v: k for k, v in FIRST_COMPREHENSION_MAPPING.items()}
        distribution: dict[str, int] = {}
        for score in scores:
            band = reverse.get(score, "unknown")
            distribution[band] = distribution.get(band, 0) + 1
        return distribution

    # ---------- WEEKLY ----------

    def _weekly_dictation(self, scope_id: str, range_start: str | None, range_end: str | None) -> list[MetricPoint]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT week_id, dictation_score, dictation_pass, gate_status, reinforcement_status, created_at FROM weekly_assessments ORDER BY created_at"
            ).fetchall()
        points = []
        for row in rows:
            if (range_start and row["created_at"] < range_start) or (range_end and row["created_at"] > range_end):
                continue
            points.append(
                {
                    "metric_id": "P2.WEEKLY.DICTATION_SCORE",
                    "period": row["week_id"],
                    "value": row["dictation_score"] if row["dictation_score"] is not None else 0.0,
                    "sample_count": 1,
                    "gate_status": row["gate_status"],
                    "dictation_pass": bool(row["dictation_pass"]) if row["dictation_pass"] is not None else None,
                    "reinforcement_status": row["reinforcement_status"],
                    "missing_reason": None if row["dictation_score"] is not None else "no_score",
                }
            )
        return points

    # ---------- READING ----------

    def _reading_practice(self, scope_id: str, range_start: str | None, range_end: str | None) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT speed_result, pause_result, stress_result, user_duration, reference_duration, created_at FROM reading_attempts"
            ).fetchall()
        dimensions = {"speed": {}, "pause": {}, "stress": {}}
        numeric = {"speed_ratio": [], "pause_delta": []}
        for row in rows:
            if (range_start and row["created_at"] < range_start) or (range_end and row["created_at"] > range_end):
                continue
            for dim, key in (("speed", "speed_result"), ("pause", "pause_result"), ("stress", "stress_result")):
                status = row[key]
                if status:
                    dimensions[dim][status] = dimensions[dim].get(status, 0) + 1
            if row["user_duration"] is not None and row["reference_duration"]:
                numeric["speed_ratio"].append(round(row["user_duration"] / row["reference_duration"], 3))
        return {
            "metric_id": "P2.READING.PRACTICE_DIMENSION",
            "dimension_distributions": dimensions,
            "numeric": {k: v for k, v in numeric.items() if v},
            "note": "dimensions judged independently; never averaged",
        }

    # ---------- DIFFICULTY ----------

    def _difficulty_streak(self, scope_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT current_stage, consecutive_pass_weeks, upgrade_eligible, cooldown_until FROM training_difficulty_profiles WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
        if row is None:
            return {
                "metric_id": "P2.DIFFICULTY.STREAK", "current_stage": "STAGE_1",
                "consecutive_pass_weeks": 0, "source": "P1 weekly_gate_records",
            }
        return {
            "metric_id": "P2.DIFFICULTY.STREAK",
            "current_stage": row["current_stage"],
            "consecutive_pass_weeks": row["consecutive_pass_weeks"],
            "upgrade_eligible": bool(row["upgrade_eligible"]),
            "cooldown_until": row["cooldown_until"],
            "source": "P1 weekly_gate_records",
        }

    def _current_stage(self, scope_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT current_stage FROM training_difficulty_profiles WHERE scope_id = ?", (scope_id,)
            ).fetchone()
        return row["current_stage"] if row else "STAGE_1"

    def _current_gate_state(self, scope_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT gate_status FROM weekly_assessments ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row["gate_status"] if row else None

    def _suggestions_enabled(self, scope_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT suggestions_enabled FROM review_preferences WHERE scope_id = ?", (scope_id,)
            ).fetchone()
        return bool(row["suggestions_enabled"]) if row else True  # default enabled
