"""M4: weekly assessment closed loop (Spec 14/15).

- Test type follows this week's actual training (Spec 14.1).
- Dictation score below the 80% threshold forces the gate to reinforcement
  regardless of the caller's flag (Spec 14.2).
- Reinforcement is a short package from this week's weak targets, never with
  out-of-scope vocabulary (Spec 15.1); once every item is exact the gate
  recovers to WEEKLY_GATE_PASS (Spec 15.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.core.learning_time import LearningTimeService
from app.core.weekly import WeeklyAssessmentService
from app.db.connection import Database
from tests.fixtures import DEFAULT_SENTENCES, create_material, make_database, make_settings

NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)  # Wednesday of ISO week 2026-W35


def _weekly_service(database: Database, tmp_path: Path) -> WeeklyAssessmentService:
    return WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: NOW)


def _log_training(database: Database, tmp_path: Path, *activities: str) -> None:
    time_service = LearningTimeService(database, now_fn=lambda: NOW)
    for activity in activities:
        started = time_service.start(activity_type=activity)
        time_service.stop(started["time_log_id"], 60)


def test_test_type_follows_weekly_training(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    service = _weekly_service(database, tmp_path)

    _log_training(database, tmp_path, "DICTATION")
    assessment = service.create(
        week_id="W1", period_start="2026-08-24", period_end="2026-08-30"
    )
    assert assessment["dictation_required"] is True
    assert assessment["reading_required"] is False

    service.create(
        week_id="W2", period_start="2026-08-24", period_end="2026-08-30", dictation_required=True
    )
    assert service.get("W2")["reading_required"] is False

    _log_training(database, tmp_path, "READING")
    both = service.create(
        week_id="W3", period_start="2026-08-24", period_end="2026-08-30"
    )
    assert both["dictation_required"] is True
    assert both["reading_required"] is True


def test_score_below_threshold_forces_failure(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    service = _weekly_service(database, tmp_path)
    service.create(
        week_id="W1", period_start="2026-08-24", period_end="2026-08-30",
        dictation_required=True, reading_required=False,
    )
    # Caller claims pass, but 70 < 80 must fail the gate.
    result = service.record_dictation("W1", score=70, passed=True)
    assert result["dictation_pass"] is False
    assert result["gate_status"] == "REINFORCEMENT_REQUIRED"

    above = service.record_dictation("W1", score=86, passed=True)
    assert above["dictation_pass"] is True
    assert above["gate_status"] == "WEEKLY_GATE_PASS"


def test_weekly_test_items_and_auto_score(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    create_material(database, "m1")
    service = _weekly_service(database, tmp_path)
    service.create(
        week_id="W1", period_start="2026-08-24", period_end="2026-08-30",
        dictation_required=True, reading_required=False,
    )

    items = service.create_test_items("W1", count=3)
    assert len(items) == 3
    assert all(item["text"] in DEFAULT_SENTENCES for item in items)

    # Two exact, one wrong -> 66.7% < 80% -> gate fails automatically.
    service.submit_test_dictation("W1", items[0]["item_id"], items[0]["text"], listen_count=1)
    service.submit_test_dictation("W1", items[1]["item_id"], items[1]["text"], listen_count=1)
    result = service.submit_test_dictation("W1", items[2]["item_id"], "wrong wrong wrong", listen_count=1)
    assert result["is_exact_match"] is False

    assessment = service.get("W1")
    assert assessment["dictation_score"] == pytest.approx(66.7, abs=0.1)
    assert assessment["dictation_pass"] is False
    assert assessment["gate_status"] == "REINFORCEMENT_REQUIRED"


def test_reinforcement_loop_recovers_gate(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    create_material(database, "m1")
    service = _weekly_service(database, tmp_path)
    service.create(
        week_id="W1", period_start="2026-08-24", period_end="2026-08-30",
        dictation_required=True, reading_required=False,
    )
    items = service.create_test_items("W1", count=3)
    # Wrong on purpose to fail the test and leave weak targets behind.
    for item in items:
        service.submit_test_dictation("W1", item["item_id"], "wrong", listen_count=1)
    assert service.get("W1")["gate_status"] == "REINFORCEMENT_REQUIRED"

    reinforcement = service.start_reinforcement("W1")
    assert reinforcement["gate_status"] == "REINFORCEMENT"
    rein_items = reinforcement["reinforcement_items"]
    assert rein_items, "weak targets must produce reinforcement sentences"
    assert all(item["text"] in DEFAULT_SENTENCES for item in rein_items)

    for item in rein_items:
        done = service.submit_reinforcement_dictation(
            "W1", item["item_id"], item["text"], listen_count=1
        )
        assert done["is_exact_match"] is True

    # All reinforcement items exact -> targeted retest armed, gate not yet recovered.
    armed = service.get("W1")
    assert armed["gate_status"] == "TARGETED_RETEST"
    assert armed["reinforcement_status"] == "PENDING_RETEST"

    recovered = service.confirm_retest("W1")
    assert recovered["gate_status"] == "WEEKLY_GATE_PASS"
    assert recovered["reinforcement_status"] == "COMPLETED"


def test_weekly_api_round_trip(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    create_material(database, "api-m1")
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            created = client.post(
                "/api/weekly-assessments",
                json={
                    "week_id": "W-API",
                    "period_start": "2026-08-24",
                    "period_end": "2026-08-30",
                    "dictation_required": True,
                    "reading_required": False,
                },
            )
            assert created.status_code == 200, created.text
            assert created.json()["gate_status"] == "WEEKLY_ASSESSMENT_READY"

            items = client.post("/api/weekly-assessments/W-API/test-items", json={"count": 2})
            assert items.status_code == 200, items.text
            assert len(items.json()) == 2

            first = client.post(
                f"/api/weekly-assessments/W-API/test-items/{items.json()[0]['item_id']}/dictation",
                json={"user_text": items.json()[0]["text"], "listen_count": 1},
            )
            assert first.status_code == 200
            assert first.json()["is_exact_match"] is True
    finally:
        main_module.settings = original_settings
