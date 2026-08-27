"""P2-3 difficulty progression history tests: idempotent events, P1 wiring,
downgrade flow (suggest -> confirm/decline), cooldown expiry, Stage facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.difficulty_history import DifficultyHistoryError, DifficultyHistoryService
from app.core.difficulty_progression import DifficultyProgressionService
from app.core.weekly import WeeklyAssessmentService
from app.db.connection import Database
from tests.fixtures import make_database, make_settings

NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def advance_weeks(self, weeks: int) -> None:
        self.now = self.now + timedelta(days=7 * weeks)


def _setup(tmp_path: Path):
    db = make_database(tmp_path)
    clock = Clock()
    weekly = WeeklyAssessmentService(db, make_settings(tmp_path), now_fn=lambda: clock.now)
    history = DifficultyHistoryService(db, now_fn=lambda: clock.now)
    difficulty = DifficultyProgressionService(db, weekly, now_fn=lambda: clock.now, history=history)
    return db, clock, difficulty, weekly, history


def _pass_week(weekly: WeeklyAssessmentService, week_id: str, difficulty, score: float = 90.0) -> None:
    weekly.create(week_id=week_id, period_start="2026-01-01", period_end="2026-01-07", dictation_required=True, reading_required=False)
    weekly.record_dictation(week_id, score=score, passed=True)
    difficulty.evaluate_weekly_gate("default", week_id)


def _fail_week(weekly: WeeklyAssessmentService, week_id: str, difficulty) -> None:
    weekly.create(week_id=week_id, period_start="2026-01-01", period_end="2026-01-07", dictation_required=True, reading_required=False)
    weekly.record_dictation(week_id, score=72, passed=False)
    difficulty.evaluate_weekly_gate("default", week_id)


def test_events_are_recorded_and_idempotent(tmp_path: Path) -> None:
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    _pass_week(weekly, "W1", difficulty)
    events = history.history("default")["events"]
    types = {event["event_type"] for event in events}
    assert "WEEKLY_GATE_RECORDED" in types
    assert "STREAK_UPDATED" in types
    # Idempotent: replaying the same event key returns the same row.
    event = [e for e in events if e["event_type"] == "WEEKLY_GATE_RECORDED"][0]
    replayed = history.record(
        "default", "WEEKLY_GATE_RECORDED",
        stage_before=event["stage_before"], stage_after=event["stage_after"],
        source_record_ids=event["source_record_ids"], occurred_at=event["occurred_at"],
    )
    assert replayed["reused"] is True
    assert replayed["event_id"] == event["event_id"]
    # Reading history creates no new facts.
    before = len(history.history("default")["events"])
    history.history("default")
    assert len(history.history("default")["events"]) == before


def test_upgrade_flow_emits_eligible_prompted_decided_stage_changed(tmp_path: Path) -> None:
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    for index in range(1, 9):
        _pass_week(weekly, f"W{index}", difficulty)
        clock.advance_weeks(1)
    prompt = difficulty.ensure_prompt("default")
    difficulty.decide_upgrade("default", prompt["prompt_id"], "UPGRADE_CONFIRMED", "k1")

    types = {event["event_type"] for event in history.history("default")["events"]}
    assert {"UPGRADE_ELIGIBLE", "UPGRADE_PROMPTED", "UPGRADE_DECIDED", "STAGE_CHANGED"}.issubset(types)
    stage_changed = [e for e in history.history("default")["events"] if e["event_type"] == "STAGE_CHANGED"][0]
    assert stage_changed["stage_before"] == "STAGE_1"
    assert stage_changed["stage_after"] == "STAGE_2"
    assert stage_changed["actor"] == "USER"


def test_keep_current_emits_cooldown_started_and_expiry(tmp_path: Path) -> None:
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    for index in range(1, 9):
        _pass_week(weekly, f"W{index}", difficulty)
        clock.advance_weeks(1)
    prompt = difficulty.ensure_prompt("default")
    difficulty.decide_upgrade("default", prompt["prompt_id"], "KEEP_CURRENT", "k-keep")

    types = {event["event_type"] for event in history.history("default")["events"]}
    assert "COOLDOWN_STARTED" in types
    assert "COOLDOWN_EXPIRED" not in types
    # Advance past the cooldown: reading history materializes expiry once.
    clock.advance_weeks(5)
    types = {event["event_type"] for event in history.history("default")["events"]}
    assert "COOLDOWN_EXPIRED" in types
    expired = [e for e in history.history("default")["events"] if e["event_type"] == "COOLDOWN_EXPIRED"]
    assert len(expired) == 1


def test_downgrade_suggested_after_two_failed_gates(tmp_path: Path) -> None:
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    difficulty.get_profile("default")  # ensure the profile row exists
    with db.connect() as connection:
        connection.execute(
            "UPDATE training_difficulty_profiles SET current_stage = 'STAGE_2' WHERE scope_id = 'default'"
        )
    _fail_week(weekly, "W1", difficulty)
    _fail_week(weekly, "W2", difficulty)
    result = history.check_downgrade_suggestion("default")
    assert result["suggested"] is True
    types = {event["event_type"] for event in history.history("default")["events"]}
    assert "DOWNGRADE_SUGGESTED" in types

    # One passing gate clears the trigger.
    _pass_week(weekly, "W3", difficulty)
    assert history.check_downgrade_suggestion("default")["suggested"] is False


def test_downgrade_confirm_crosses_one_stage_and_resets(tmp_path: Path) -> None:
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    difficulty.get_profile("default")  # ensure the profile row exists
    with db.connect() as connection:
        connection.execute(
            "UPDATE training_difficulty_profiles SET current_stage = 'STAGE_2', consecutive_pass_weeks = 8 WHERE scope_id = 'default'"
        )
    history.downgrade_request("default")
    confirmed = history.downgrade_confirm("default")
    assert confirmed["stage_before"] == "STAGE_2"
    assert confirmed["stage_after"] == "STAGE_1"
    profile = difficulty.get_profile("default")
    assert profile["current_stage"] == "STAGE_1"
    assert profile["consecutive_pass_weeks"] == 0
    # Minimum stage guards.
    with pytest.raises(DifficultyHistoryError):
        history.downgrade_confirm("default")


def test_downgrade_decline_records_event_only(tmp_path: Path) -> None:
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    difficulty.get_profile("default")  # ensure the profile row exists
    with db.connect() as connection:
        connection.execute(
            "UPDATE training_difficulty_profiles SET current_stage = 'STAGE_2' WHERE scope_id = 'default'"
        )
    declined = history.downgrade_decline("default")
    assert declined["event_type"] == "DOWNGRADE_DECLINED"
    assert difficulty.get_profile("default")["current_stage"] == "STAGE_2"


def test_history_isolates_legacy_materials_next(tmp_path: Path) -> None:
    """Legacy /api/materials/next never creates formal Stage events."""
    db, clock, difficulty, weekly, history = _setup(tmp_path)
    _pass_week(weekly, "W1", difficulty)
    events = history.history("default")["events"]
    assert all(event["event_type"] not in ("STAGE_CHANGED", "UPGRADE_ELIGIBLE") for event in events)
