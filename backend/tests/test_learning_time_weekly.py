from pathlib import Path

import pytest

from app.config import Settings
from app.core.learning_time import LearningTimeService
from app.core.weekly import WeeklyAssessmentService
from app.db.connection import Database
from tests.fixtures import make_settings


def _database(tmp_path: Path) -> Database:
    database = Database(make_settings(tmp_path))
    database.initialize()
    return database


def test_learning_time_is_aggregated_once(tmp_path: Path) -> None:
    service = LearningTimeService(_database(tmp_path))

    started = service.start(activity_type="DICTATION", session_id="session-1")
    stopped = service.stop(started["time_log_id"], 42)

    assert stopped["active_seconds"] == 42
    assert service.stats()["total_learning_seconds"] == 42
    with pytest.raises(ValueError, match="already been stopped"):
        service.stop(started["time_log_id"], 42)


def test_weekly_gate_requires_all_required_components(tmp_path: Path) -> None:
    database = _database(tmp_path)
    service = WeeklyAssessmentService(database, make_settings(tmp_path))
    service.create(
        week_id="2026-W35",
        period_start="2026-08-24",
        period_end="2026-08-30",
        dictation_required=True,
        reading_required=True,
    )

    pending = service.evaluate("2026-W35")
    assert pending["gate_status"] == "REINFORCEMENT_REQUIRED"
    assert pending["reinforcement_status"] == "REINFORCEMENT_REQUIRED"

    service.record_dictation("2026-W35", score=86, passed=True)
    failed_reading = service.record_reading(
        "2026-W35", {"speed": True, "pause": False, "stress": True}
    )
    assert failed_reading["gate_status"] == "REINFORCEMENT_REQUIRED"

    passed = service.record_reading(
        "2026-W35", {"speed": True, "pause": True, "stress": True}
    )
    assert passed["gate_status"] == "WEEKLY_GATE_PASS"
    assert passed["reinforcement_status"] == "NOT_REQUIRED"

