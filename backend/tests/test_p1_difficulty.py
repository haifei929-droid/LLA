"""P1 difficulty progression: idempotent gate records, 8-week stability,
4-week cooldown, single-variable upgrade, Stage 3 cap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.difficulty_progression import (
    COOLDOWN_DAYS,
    DifficultyError,
    DifficultyProgressionService,
    STABLE_WEEKS_REQUIRED,
)
from app.core.weekly import WeeklyAssessmentService
from app.db.connection import Database
from tests.fixtures import make_database, make_settings

NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def advance(self, days: int) -> None:
        self.now = self.now + timedelta(days=days)


def _setup(tmp_path: Path) -> tuple[Database, Clock, DifficultyProgressionService, WeeklyAssessmentService]:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    clock = Clock()
    weekly = WeeklyAssessmentService(database, settings, now_fn=lambda: clock.now)
    difficulty = DifficultyProgressionService(database, weekly, now_fn=lambda: clock.now)
    return database, clock, difficulty, weekly


def _week_passed(weekly: WeeklyAssessmentService, week_id: str, score: float = 90.0) -> None:
    weekly.create(
        week_id=week_id, period_start="2026-01-01", period_end="2026-01-07",
        dictation_required=True, reading_required=False,
    )
    weekly.record_dictation(week_id, score=score, passed=score >= 80)


def _week_failed(weekly: WeeklyAssessmentService, week_id: str) -> None:
    weekly.create(
        week_id=week_id, period_start="2026-01-01", period_end="2026-01-07",
        dictation_required=True, reading_required=False,
    )
    weekly.record_dictation(week_id, score=72, passed=False)


def test_consecutive_pass_weeks_and_eligibility(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 9):
        _week_passed(weekly, f"W{index}")
        result = difficulty.evaluate_weekly_gate("default", f"W{index}")
        assert result["record"]["gate_result"] == "PASS"

    profile = difficulty.get_profile("default")
    assert profile["consecutive_pass_weeks"] == STABLE_WEEKS_REQUIRED
    assert profile["upgrade_eligible"] is True

    prompt = difficulty.ensure_prompt("default")
    assert prompt["prompt_status"] == "PENDING"
    # Same stage prompt is idempotent.
    assert difficulty.ensure_prompt("default")["prompt_id"] == prompt["prompt_id"]


def test_failure_resets_the_streak(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 5):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    _week_failed(weekly, "W5")
    difficulty.evaluate_weekly_gate("default", "W5")
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 0
    assert difficulty.get_profile("default")["upgrade_eligible"] is False


def test_below_80_dictation_counts_as_fail(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 8):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    # Week 8: gate row says PASS (P0 forced pass=False below 80, so use 79 via P0).
    _week_passed(weekly, "W8", score=79)
    difficulty.evaluate_weekly_gate("default", "W8")
    record = difficulty._latest_record("default")
    assert record["gate_result"] == "FAIL"
    assert "dictation_below_80" in record["evaluation_reason_codes"]
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 0


def test_reading_failure_breaks_the_streak(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 8):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    weekly.create(
        week_id="W8", period_start="2026-01-01", period_end="2026-01-07",
        dictation_required=True, reading_required=True,
    )
    weekly.record_dictation("W8", score=90, passed=True)
    weekly.record_reading("W8", {"speed": True, "pause": False, "stress": True})
    difficulty.evaluate_weekly_gate("default", "W8")
    record = difficulty._latest_record("default")
    assert record["read_aloud_attempted"] == 1
    assert record["read_aloud_score"] == "FAIL"
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 0


def test_gate_record_is_idempotent(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    _week_passed(weekly, "W1")
    first = difficulty.evaluate_weekly_gate("default", "W1")
    second = difficulty.evaluate_weekly_gate("default", "W1")
    assert second["reused"] is True
    assert second["record"]["gate_id"] == first["record"]["gate_id"]
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1


def test_upgrade_confirmed_moves_one_stage_and_resets(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 9):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    prompt = difficulty.ensure_prompt("default")

    decided = difficulty.decide_upgrade("default", prompt["prompt_id"], "UPGRADE_CONFIRMED", "key-1")
    profile = decided["profile"]
    assert profile["current_stage"] == "STAGE_2"
    assert profile["consecutive_pass_weeks"] == 0
    assert profile["upgrade_eligible"] is False
    assert profile["last_upgrade_decision"] == "UPGRADE_CONFIRMED"
    assert profile["last_upgrade_at"] is not None

    # Replayed decision returns the first result.
    replayed = difficulty.decide_upgrade("default", prompt["prompt_id"], "UPGRADE_CONFIRMED", "key-2")
    assert replayed["reused"] is True
    assert replayed["profile"]["current_stage"] == "STAGE_2"


def test_keep_current_starts_cooldown(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 9):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    prompt = difficulty.ensure_prompt("default")

    decided = difficulty.decide_upgrade("default", prompt["prompt_id"], "KEEP_CURRENT", "key-keep")
    profile = decided["profile"]
    assert profile["current_stage"] == "STAGE_1"
    assert profile["consecutive_pass_weeks"] == STABLE_WEEKS_REQUIRED
    assert profile["upgrade_eligible"] is False
    assert profile["cooldown_until"] is not None

    # During cooldown no new prompt can be generated.
    with pytest.raises(DifficultyError) as exc:
        difficulty.ensure_prompt("default")
    assert exc.value.code == "NOT_ELIGIBLE"

    # After the cooldown, eligibility returns and a prompt can be regenerated.
    clock.advance(COOLDOWN_DAYS + 1)
    profile = difficulty.get_profile("default")
    assert profile["upgrade_eligible"] is True
    new_prompt = difficulty.ensure_prompt("default")
    assert new_prompt["prompt_status"] == "PENDING"
    assert new_prompt["prompt_id"] != prompt["prompt_id"]


def test_stage_three_is_capped(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    difficulty.get_profile("default")  # ensure the profile row exists
    with database.connect() as connection:
        connection.execute(
            "UPDATE training_difficulty_profiles SET current_stage = 'STAGE_3' WHERE scope_id = 'default'"
        )
    with pytest.raises(DifficultyError) as exc:
        difficulty.ensure_prompt("default")
    assert exc.value.code == "MAX_STAGE_REACHED"


def test_week_gap_resets_sequence(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 8):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    clock.advance(35)  # big gap
    _week_passed(weekly, "W9")
    difficulty.evaluate_weekly_gate("default", "W9")
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1


def test_api_profile_and_decision_round_trip(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app import main as main_module

    database, clock, difficulty, weekly = _setup(tmp_path)
    for index in range(1, 9):
        _week_passed(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
    prompt = difficulty.ensure_prompt("default")

    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            profile = client.get("/api/p1/difficulty/profile?scope_id=default").json()
            assert profile["upgrade_eligible"] is True

            decided = client.post(
                "/api/p1/difficulty/upgrade-decision",
                json={
                    "scope_id": "default",
                    "decision": "DECIDE_LATER",
                    "prompt_id": prompt["prompt_id"],
                    "idempotency_key": "api-decision-1",
                },
            )
            assert decided.status_code == 200, decided.text
            assert decided.json()["decision"] == "DECIDE_LATER"
            assert decided.json()["profile"]["upgrade_eligible"] is False

            replayed = client.post(
                "/api/p1/difficulty/upgrade-decision",
                json={
                    "scope_id": "default",
                    "decision": "UPGRADE_CONFIRMED",
                    "prompt_id": prompt["prompt_id"],
                    "idempotency_key": "api-decision-2",
                },
            )
            assert replayed.status_code == 200
            assert replayed.json()["reused"] is True
            assert replayed.json()["decision"] == "DECIDE_LATER"
    finally:
        main_module.settings = original_settings
