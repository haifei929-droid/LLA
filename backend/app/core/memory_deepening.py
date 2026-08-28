"""P2-2 Listening Memory deepening (P2 spec 9).

Four layers: normalized Target, TargetOccurrence (material/sentence/part),
RecognitionEpisode (one sentence-level listening session aggregating retries),
and TargetAggregate (cross-material summary). Only ordinary-training evidence
is used: Weekly Test, SPELLING errors, Hint-after-correct and Reveal-only
recognition never enter the average first-correct-listen metric; Reveal-only
targets may still qualify as difficulty evidence.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.dictation import normalize_for_match
from app.db.connection import Database

#: Confirmed visible defaults (P2 9.3); the user may change them, and an
#: explicit version accompanies every result. No hidden defaults: if the user
#: has not selected a configuration, classification returns UNCONFIGURED.
DEFAULT_THRESHOLDS = {
    "short_days": 14,
    "long_days": 56,  # 8 weeks
    "min_episodes": 3,
    "min_dates": 2,
}
DEFAULT_CONFIG_VERSION = "1.0"

DIFFICULTY_CLASSES = ("SHORT_TERM_DIFFICULT", "LONG_TERM_DIFFICULT", "RECOVERED", "NOT_CLASSIFIED")


class MemoryConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MemoryDeepeningService:
    def __init__(self, database: Database, now_fn=None) -> None:
        self.database = database
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    # ---------- episodes & occurrences (built from dictation_attempts) ----------

    def build_episodes(self, scope_id: str) -> int:
        """Derive recognition episodes from ordinary dictation attempts.

        One episode per (sentence, target) per date, aggregating retries:
        first exact listen_count (where not revealed and hint_level=0) and
        any Hint/Reveal usage across the retries.
        """
        with self.database.connect() as connection:
            attempts = connection.execute(
                """
                SELECT a.attempt_id, a.sentence_id, a.user_text, a.listen_count, a.hint_level,
                       a.revealed, a.error_details, a.created_at,
                       s.text AS expected_text, s.material_id, s.part_no
                  FROM dictation_attempts a
                  JOIN sentences s ON s.sentence_id = a.sentence_id
                 ORDER BY a.created_at ASC
                """
            ).fetchall()

        # Sentence-level target set: the union of non-SPELLING error words
        # across all attempts of the sentence. An exact attempt must update
        # the same targets (it is the recognition of those words), so the
        # first exact listen_count lands on the episode.
        sentence_targets: dict[str, set[str]] = {}
        for attempt in attempts:
            errors = json.loads(attempt["error_details"])
            for error in errors:
                expected = error.get("expected")
                error_type = error.get("error_type")
                if expected and error_type not in ("SPELLING", "ACTIVE_BLANK"):
                    sentence_targets.setdefault(attempt["sentence_id"], set()).add(
                        normalize_for_match(expected)
                    )

        episodes: dict[tuple[str, str, str], dict[str, Any]] = {}
        for attempt in attempts:
            date_key = datetime.fromisoformat(attempt["created_at"]).date().isoformat()
            exact = self._attempt_was_exact(attempt)
            for target in sentence_targets.get(attempt["sentence_id"], set()):
                key = (attempt["sentence_id"], target, date_key)
                episode = episodes.setdefault(
                    key,
                    {
                        "target": target,
                        "sentence_id": attempt["sentence_id"],
                        "material_id": attempt["material_id"],
                        "part_no": attempt["part_no"],
                        "date": date_key,
                        "first_exact_listen_count": None,
                        "revealed": 0,
                        "hint_used": 0,
                        "attempt_count": 0,
                    },
                )
                episode["attempt_count"] += 1
                if attempt["revealed"]:
                    episode["revealed"] = 1
                if attempt["hint_level"]:
                    episode["hint_used"] = 1
                if (
                    episode["first_exact_listen_count"] is None
                    and exact
                    and not attempt["revealed"]
                    and attempt["hint_level"] == 0
                ):
                    episode["first_exact_listen_count"] = attempt["listen_count"]

        created = 0
        source_ids: list[str] = []
        with self.database.connect() as connection:
            for episode in episodes.values():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO memory_target_occurrences(
                        occurrence_id, scope_id, target, material_id, sentence_id, part_no, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid4()), scope_id, episode["target"], episode["material_id"],
                     episode["sentence_id"], episode["part_no"], self.now_fn().isoformat()),
                )
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO memory_recognition_episodes(
                        episode_id, scope_id, target, occurrence_id, sentence_id,
                        first_exact_listen_count, revealed, hint_used, attempt_count,
                        episode_date, backfilled, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        str(uuid4()), scope_id, episode["target"],
                        self._occurrence_id(connection, scope_id, episode["sentence_id"], episode["target"]),
                        episode["sentence_id"], episode["first_exact_listen_count"],
                        episode["revealed"], episode["hint_used"], episode["attempt_count"],
                        episode["date"], self.now_fn().isoformat(),
                    ),
                )
                if inserted.rowcount:
                    created += 1
            # Mandatory backfill audit (P2 spec 10.1): every backfill writes an
            # audit row with source linkage, derived fields, versions and
            # reliability. Fields like first-correct/episode boundaries are
            # derived from attempt linkage, so they are CONDITIONAL per 10.3.
            source_ids = [row["attempt_id"] for row in attempts]
            connection.execute(
                """
                INSERT INTO backfill_audits(
                    audit_id, scope_id, source_record_ids_json, fields_derived_json,
                    source_schema_version, metric_version, reliability,
                    unavailable_reasons_json, backfilled_at
                ) VALUES (?, ?, ?, ?, '1.0', '1.0', 'CONDITIONAL', '{}', ?)
                """,
                (
                    str(uuid4()), scope_id,
                    json.dumps(source_ids[:500]),
                    json.dumps(
                        ["memory_target_occurrences", "memory_recognition_episodes",
                         "first_exact_listen_count", "revealed", "hint_used", "attempt_count"],
                        sort_keys=True,
                    ),
                    self.now_fn().isoformat(),
                ),
            )
        return created

    @staticmethod
    def _attempt_was_exact(attempt: Any) -> bool:
        from app.core.dictation import evaluate_dictation

        return evaluate_dictation(attempt["expected_text"], attempt["user_text"]).is_exact_match

    def _occurrence_id(self, connection, scope_id: str, sentence_id: str, target: str) -> str:
        row = connection.execute(
            "SELECT occurrence_id FROM memory_target_occurrences WHERE scope_id = ? AND sentence_id = ? AND target = ?",
            (scope_id, sentence_id, target),
        ).fetchone()
        return row["occurrence_id"]

    # ---------- threshold configuration ----------

    def get_config(self, scope_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_threshold_configs WHERE scope_id = ?", (scope_id,)
            ).fetchone()
        if row is None:
            return {"configured": False, "config_version": None}
        return {
            "configured": True,
            "short_days": row["short_days"],
            "long_days": row["long_days"],
            "min_episodes": row["min_episodes"],
            "min_dates": row["min_dates"],
            "config_version": row["config_version"],
        }

    def save_config(self, scope_id: str, *, short_days: int, long_days: int, min_episodes: int, min_dates: int) -> dict[str, Any]:
        if short_days < 1 or long_days < 1 or min_episodes < 1 or min_dates < 1:
            raise MemoryConfigError("INVALID_CONFIG", "all values must be positive")
        if short_days > long_days:
            raise MemoryConfigError("INVALID_CONFIG", "short-term window cannot exceed long-term window")
        previous = self.get_config(scope_id)
        version = _bump_version(previous.get("config_version")) if previous.get("config_version") else DEFAULT_CONFIG_VERSION
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_threshold_configs(
                    scope_id, short_days, long_days, min_episodes, min_dates, config_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id) DO UPDATE SET
                    short_days = excluded.short_days, long_days = excluded.long_days,
                    min_episodes = excluded.min_episodes, min_dates = excluded.min_dates,
                    config_version = excluded.config_version, updated_at = excluded.updated_at
                """,
                (scope_id, short_days, long_days, min_episodes, min_dates, version, self.now_fn().isoformat()),
            )
        result = self.get_config(scope_id)
        result["validation"] = "ok"
        result["effective_at"] = self.now_fn().isoformat()
        return result

    # ---------- aggregates & classification ----------

    def read_memory(self, scope_id: str) -> dict[str, Any]:
        config = self.get_config(scope_id)
        if not config["configured"]:
            return {
                "configured": False,
                "classification_status": "UNCONFIGURED",
                "targets": [],
                "thresholds": None,
                "message": "user threshold configuration required before classification",
            }
        now = self.now_fn()
        long_cutoff = now - timedelta(days=config["long_days"])
        short_cutoff = now - timedelta(days=config["short_days"])

        with self.database.connect() as connection:
            episodes = connection.execute(
                """
                SELECT target, episode_date, first_exact_listen_count, revealed, hint_used
                  FROM memory_recognition_episodes WHERE scope_id = ?
                """,
                (scope_id,),
            ).fetchall()

        aggregates: dict[str, dict[str, Any]] = {}
        for episode in episodes:
            target = episode["target"]
            aggregate = aggregates.setdefault(
                target,
                {
                    "target": target,
                    "occurrences": 0,
                    "qualifying_episodes": 0,
                    "distinct_dates": set(),
                    "first_correct_listen_counts": [],
                    "hint_count": 0,
                    "reveal_count": 0,
                    "recent_short_term_episodes": 0,
                    "recent_long_term_episodes": 0,
                },
            )
            aggregate["occurrences"] += 1
            date = datetime.fromisoformat(episode["episode_date"]).replace(tzinfo=UTC)
            # Short/long windows on qualifying episodes only (non-SPELLING
            # evidence present, i.e. the episode exists at all here).
            if date >= short_cutoff:
                aggregate["recent_short_term_episodes"] += 1
            if date >= long_cutoff:
                aggregate["recent_long_term_episodes"] += 1
                aggregate["qualifying_episodes"] += 1
                aggregate["distinct_dates"].add(episode["episode_date"])
            if episode["hint_used"]:
                aggregate["hint_count"] += 1
            if episode["revealed"]:
                aggregate["reveal_count"] += 1
            # First-correct metric: exclude revealed and hint-after-correct.
            if episode["first_exact_listen_count"] is not None and not episode["revealed"] and not episode["hint_used"]:
                aggregate["first_correct_listen_counts"].append(episode["first_exact_listen_count"])

        targets = []
        for target, aggregate in aggregates.items():
            distinct_dates = len(aggregate["distinct_dates"])
            avg_first_correct = (
                round(sum(aggregate["first_correct_listen_counts"]) / len(aggregate["first_correct_listen_counts"]), 2)
                if aggregate["first_correct_listen_counts"]
                else None
            )
            classification = self._classify(
                aggregate["recent_short_term_episodes"],
                aggregate["recent_long_term_episodes"],
                distinct_dates,
                config,
                avg_first_correct,
            )
            confidence = round(
                min(
                    1.0,
                    0.5 * aggregate["qualifying_episodes"] / config["min_episodes"]
                    + 0.5 * distinct_dates / config["min_dates"],
                ),
                2,
            )
            targets.append(
                {
                    "target": target,
                    "occurrences": aggregate["occurrences"],
                    "qualifying_episodes": aggregate["qualifying_episodes"],
                    "distinct_dates": distinct_dates,
                    "average_first_correct_listen": avg_first_correct,
                    "first_correct_distribution": _distribution(aggregate["first_correct_listen_counts"]),
                    "hint_count": aggregate["hint_count"],
                    "reveal_count": aggregate["reveal_count"],
                    "difficulty_classification": classification,
                    "confidence": confidence,
                    "thresholds_applied": {
                        "short_days": config["short_days"],
                        "long_days": config["long_days"],
                        "min_episodes": config["min_episodes"],
                        "min_dates": config["min_dates"],
                        "config_version": config["config_version"],
                    },
                }
            )
        targets.sort(key=lambda t: (t["difficulty_classification"] == "NOT_CLASSIFIED", -t["qualifying_episodes"]))
        return {
            "configured": True,
            "classification_status": "CLASSIFIED",
            "thresholds": config,
            "targets": targets,
            "suggestions": self.list_suggestions(scope_id),
        }

    @staticmethod
    def _classify(short_episodes: int, long_episodes: int, distinct_dates: int, config: dict[str, Any], avg_first_correct: float | None) -> str:
        if long_episodes < config["min_episodes"] or distinct_dates < config["min_dates"]:
            return "NOT_CLASSIFIED"
        if short_episodes >= config["min_episodes"] and short_episodes >= long_episodes:
            return "SHORT_TERM_DIFFICULT"
        if avg_first_correct is not None and avg_first_correct >= 3:
            return "LONG_TERM_DIFFICULT"
        if short_episodes == 0:
            return "RECOVERED"
        return "NOT_CLASSIFIED"

    # ---------- review suggestions ----------

    def _default_preferences(self, scope_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_preferences WHERE scope_id = ?", (scope_id,)
            ).fetchone()
        if row is None:
            return {
                "suggestions_enabled": True,
                "batch_paused": False,
                "global_paused": False,
                "snoozed_until": None,
                "frequency_days": 7,
                "per_target_disabled": {},
            }
        return {
            "suggestions_enabled": bool(row["suggestions_enabled"]),
            "batch_paused": bool(row["batch_paused"]),
            "global_paused": bool(row["global_paused"]),
            "snoozed_until": row["snoozed_until"],
            "frequency_days": row["frequency_days"],
            "per_target_disabled": json.loads(row["per_target_disabled_json"]),
        }

    def list_suggestions(self, scope_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_suggestions WHERE scope_id = ? ORDER BY created_at DESC LIMIT 50",
                (scope_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def generate_suggestions(self, scope_id: str) -> dict[str, Any]:
        """Suggestions are enabled by default; evidence-based, never write to
        P0/P1 state. Same target at most once per frequency window."""
        prefs = self._default_preferences(scope_id)
        now = self.now_fn()
        result = self.read_memory(scope_id)
        if not prefs["suggestions_enabled"] or prefs["global_paused"] or prefs["batch_paused"]:
            return {"suggestions_enabled": prefs["suggestions_enabled"], "generated": 0, "reason": "suggestions paused or disabled"}
        if prefs["snoozed_until"] and now < datetime.fromisoformat(prefs["snoozed_until"]):
            return {"suggestions_enabled": True, "generated": 0, "reason": "snoozed"}

        generated = 0
        for target in result.get("targets", []):
            name = target["target"]
            if name in prefs["per_target_disabled"]:
                continue
            classification = target["difficulty_classification"]
            if classification not in ("SHORT_TERM_DIFFICULT", "LONG_TERM_DIFFICULT"):
                continue
            # Frequency limit: at most once per frequency_days.
            with self.database.connect() as connection:
                recent = connection.execute(
                    """
                    SELECT 1 FROM review_suggestions
                     WHERE scope_id = ? AND target = ? AND created_at >= ?
                     LIMIT 1
                    """,
                    (scope_id, name, (now - timedelta(days=prefs["frequency_days"])).isoformat()),
                ).fetchone()
            if recent is not None:
                continue
            evidence = {
                "classification": classification,
                "qualifying_episodes": target["qualifying_episodes"],
                "distinct_dates": target["distinct_dates"],
                "average_first_correct_listen": target["average_first_correct_listen"],
                "hint_count": target["hint_count"],
                "reveal_count": target["reveal_count"],
            }
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO review_suggestions(
                        suggestion_id, scope_id, target, trigger_evidence_json, status, created_at
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', ?)
                    """,
                    (str(uuid4()), scope_id, name, json.dumps(evidence, ensure_ascii=False), now.isoformat()),
                )
            generated += 1
        return {"suggestions_enabled": True, "generated": generated, "reason": "ok"}

    def update_preferences(self, scope_id: str, *, action: str, target: str | None = None) -> dict[str, Any]:
        prefs = self._default_preferences(scope_id)
        now = self.now_fn()
        if action == "disable_target":
            if not target:
                raise MemoryConfigError("INVALID_ACTION", "target required")
            prefs["per_target_disabled"][target] = now.isoformat()
        elif action == "snooze_target":
            if not target:
                raise MemoryConfigError("INVALID_ACTION", "target required")
            prefs["per_target_disabled"][target] = now.isoformat()
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE review_suggestions SET status = 'SNOOZED', snoozed_until = ? WHERE scope_id = ? AND target = ? AND status = 'ACTIVE'",
                    ((now + timedelta(days=prefs["frequency_days"])).isoformat(), scope_id, target),
                )
        elif action == "batch_pause":
            prefs["batch_paused"] = not prefs["batch_paused"]
        elif action == "global_disable":
            prefs["global_paused"] = not prefs["global_paused"]
        elif action == "restore":
            prefs["per_target_disabled"] = {}
            prefs["batch_paused"] = False
            prefs["global_paused"] = False
            prefs["snoozed_until"] = None
        elif action == "delete_suggestion_history":
            with self.database.connect() as connection:
                connection.execute("DELETE FROM review_suggestions WHERE scope_id = ?", (scope_id,))
            return {"action": action, "ok": True}
        else:
            raise MemoryConfigError("INVALID_ACTION", f"unknown action {action}")

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO review_preferences(
                    scope_id, suggestions_enabled, batch_paused, global_paused, snoozed_until,
                    frequency_days, per_target_disabled_json, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id) DO UPDATE SET
                    batch_paused = excluded.batch_paused, global_paused = excluded.global_paused,
                    snoozed_until = excluded.snoozed_until, per_target_disabled_json = excluded.per_target_disabled_json,
                    updated_at = excluded.updated_at
                """,
                (
                    scope_id,
                    int(prefs["batch_paused"]),
                    int(prefs["global_paused"]),
                    prefs["snoozed_until"],
                    prefs["frequency_days"],
                    json.dumps(prefs["per_target_disabled"], ensure_ascii=False),
                    now.isoformat(),
                ),
            )
        return {"action": action, "ok": True}


def _bump_version(version: str | None) -> str:
    try:
        major, minor = (version or DEFAULT_CONFIG_VERSION).split(".")
        return f"{major}.{int(minor) + 1}"
    except ValueError:
        return DEFAULT_CONFIG_VERSION


def _distribution(values: list[int]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for value in values:
        key = str(value)
        distribution[key] = distribution.get(key, 0) + 1
    return distribution
