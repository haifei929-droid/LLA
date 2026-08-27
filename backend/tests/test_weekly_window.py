"""Weekly-window semantics for learning time aggregation (Spec 13.1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.core.learning_time import LearningTimeService
from app.db.connection import Database

#: Wednesday of ISO week 2026-W35; the calendar window for it starts on
#: Monday 2026-08-24, and 2026-08-19 is the previous Wednesday.
NOW = datetime(2026, 8, 26, 10, 0, 0)


def _sequence_clock(times: list[datetime]) -> Callable[[], datetime]:
    """Clock that returns each time in order and then repeats the last one."""

    position = 0

    def now_fn() -> datetime:
        nonlocal position
        value = times[min(position, len(times) - 1)]
        position += 1
        return value

    return now_fn


def _service(
    tmp_path: Path,
    *,
    weekly_window: str = "calendar",
    now_fn: Callable[[], datetime] | None = None,
    db_name: str = "test.sqlite3",
) -> LearningTimeService:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / db_name,
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
        weekly_window=weekly_window,
    )
    database = Database(settings)
    database.initialize()
    return LearningTimeService(database, now_fn=now_fn)


def test_weekly_includes_log_started_inside_window(tmp_path: Path) -> None:
    service = _service(tmp_path, now_fn=lambda: NOW)

    started = service.start(activity_type="DICTATION")
    service.stop(started["time_log_id"], 42)

    assert service.stats()["weekly_learning_seconds"] == 42


def test_weekly_excludes_log_started_outside_window(tmp_path: Path) -> None:
    clock = _sequence_clock([NOW - timedelta(days=14), NOW, NOW])
    service = _service(tmp_path, now_fn=clock)

    started = service.start(activity_type="DICTATION")
    service.stop(started["time_log_id"], 42)

    assert service.stats()["weekly_learning_seconds"] == 0


def test_weekly_sums_all_stopped_logs_in_window(tmp_path: Path) -> None:
    service = _service(tmp_path, now_fn=lambda: NOW)

    first = service.start(activity_type="DICTATION")
    service.stop(first["time_log_id"], 42)
    second = service.start(activity_type="READING")
    service.stop(second["time_log_id"], 58)

    assert service.stats()["weekly_learning_seconds"] == 100


def test_weekly_calendar_excludes_last_week_log_but_rolling7_includes(
    tmp_path: Path,
) -> None:
    last_wednesday = NOW - timedelta(days=7)

    calendar = _service(
        tmp_path,
        weekly_window="calendar",
        now_fn=_sequence_clock([last_wednesday, NOW, NOW]),
    )
    calendar_log = calendar.start(activity_type="DICTATION")
    calendar.stop(calendar_log["time_log_id"], 42)
    assert calendar.stats()["weekly_learning_seconds"] == 0

    rolling = _service(
        tmp_path,
        weekly_window="rolling7",
        now_fn=_sequence_clock([last_wednesday, NOW, NOW]),
        db_name="rolling.sqlite3",
    )
    rolling_log = rolling.start(activity_type="DICTATION")
    rolling.stop(rolling_log["time_log_id"], 42)
    assert rolling.stats()["weekly_learning_seconds"] == 42


def test_weekly_ignores_active_log(tmp_path: Path) -> None:
    service = _service(tmp_path, now_fn=lambda: NOW)

    service.start(activity_type="DICTATION")

    assert service.stats()["weekly_learning_seconds"] == 0


def test_weekly_window_invalid_value_raises(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
        weekly_window="monthly",
    )
    database = Database(settings)
    database.initialize()

    with pytest.raises(ValueError, match="weekly_window"):
        LearningTimeService(database)


def test_start_rejects_unknown_activity_type(tmp_path: Path) -> None:
    service = _service(tmp_path, now_fn=lambda: NOW)

    with pytest.raises(ValueError, match="Unknown activity_type"):
        service.start(activity_type="SLEEPING")


def test_settings_from_env_reads_weekly_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTA_WEEKLY_WINDOW", "rolling7")

    assert Settings.from_env().weekly_window == "rolling7"
