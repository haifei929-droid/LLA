"""Shared weekly-window boundary used by time stats and weekly assessment."""

from __future__ import annotations

from datetime import datetime, timedelta


def week_window_start(now: datetime, policy: str) -> datetime:
    """Start boundary of the weekly window containing `now`.

    "calendar" = ISO week starting Monday 00:00; "rolling7" = trailing 7 days.
    """
    if policy == "calendar":
        return (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if policy == "rolling7":
        return now - timedelta(days=7)
    raise ValueError(f"Unknown weekly_window: {policy}")
