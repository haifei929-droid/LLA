"""P2-1 dashboard read-model tests: time aggregation, frozen comprehension
mapping, weekly dictation trend, reading dimensions, streak read."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.dashboard import FIRST_COMPREHENSION_MAPPING, DashboardService
from app.db.connection import Database
from tests.fixtures import make_database, make_settings


def _seed_material(db: Database, material_id: str = "dash-m1", sentences: int = 6) -> None:
    from app.preprocess.material import MaterialPreprocessor, TimestampedSentence

    timestamped = [
        TimestampedSentence(f"Sentence {i}.", (i - 1) * 10.0, i * 10.0)
        for i in range(1, sentences + 1)
    ]
    material = MaterialPreprocessor().process(
        material_id=material_id, title="Dash material", audio_path="dash.wav",
        transcript=" ".join(s.text for s in timestamped), timestamped_sentences=timestamped,
    )
    from app.core.materials import MaterialStore

    MaterialStore(db).create(material)


def _seed_first_comprehension(db: Database, material_id: str, rating: str) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO comprehension_checks(check_id, material_id, phase, self_rating, summary, created_at)
            VALUES (?, ?, 'FIRST', ?, 'summary', ?)
            """,
            (f"cc-{material_id}", material_id, rating, "2026-08-01T10:00:00+00:00"),
        )


def test_frozen_mapping_values() -> None:
    assert FIRST_COMPREHENSION_MAPPING == {"<30%": 15, "30–50%": 40, "50–70%": 60, ">70%": 85}


def test_dashboard_time_and_comprehension_curve(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    _seed_material(db, "dash-m1")
    _seed_material(db, "dash-m2")
    for mid, rating in (("dash-m1", "<30%"), ("dash-m2", ">70%")):
        _seed_first_comprehension(db, mid, rating)
    with db.connect() as connection:
        for i in range(3):
            connection.execute(
                "INSERT INTO training_time_logs(time_log_id, start_time, end_time, active_seconds, activity_type) VALUES (?, ?, ?, 3600, 'DICTATION')",
                (f"tl-{i}", "2026-08-01T10:00:00+00:00", "2026-08-01T11:00:00+00:00"),
            )

    service = DashboardService(db)
    result = service.read(scope_id="default")
    assert result["summary"]["total_valid_hours"] == 3.0
    assert result["summary"]["range_hours"] == 3.0
    assert result["summary"]["current_stage"] == "STAGE_1"

    curve = result["trend"]["first_comprehension_curve"]
    assert curve["sample_count"] == 2
    point = curve["points"][0]
    # 15 and 85 average -> 50.0, raw bands preserved in distribution.
    assert point["mapped_score"] == 50.0
    assert point["band_distribution"]["<30%"] == 1
    assert point["band_distribution"][">70%"] == 1
    assert point["mapping_version"] == "1.0"
    assert point["sample_count"] == 2

    time_series = result["trend"]["time_series"]
    assert time_series[0]["value"] == 3.0
    assert time_series[0]["sample_count"] == 3


def test_dashboard_range_filter(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    _seed_material(db, "dash-m1")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO training_time_logs(time_log_id, start_time, end_time, active_seconds, activity_type) VALUES ('a', '2026-08-01T10:00:00+00:00', '2026-08-01T11:00:00+00:00', 3600, 'DICTATION')",
        )
        connection.execute(
            "INSERT INTO training_time_logs(time_log_id, start_time, end_time, active_seconds, activity_type) VALUES ('b', '2026-08-10T10:00:00+00:00', '2026-08-10T11:00:00+00:00', 7200, 'READING')",
        )
    service = DashboardService(db)
    result = service.read(
        scope_id="default",
        range_start="2026-08-05T00:00:00+00:00",
        range_end="2026-08-15T00:00:00+00:00",
    )
    assert result["range_start"] == "2026-08-05T00:00:00+00:00"
    assert result["range_end"] == "2026-08-15T00:00:00+00:00"
    assert result["summary"]["range_hours"] == 2.0
    assert result["summary"]["total_valid_hours"] == 3.0

    with pytest.raises(ValueError):
        service.read(scope_id="default", range_start="2026-08-15T00:00:00+00:00", range_end="2026-08-01T00:00:00+00:00")


def test_dashboard_weekly_and_reading(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    _seed_material(db, "dash-m1")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO weekly_assessments(
                week_id, period_start, period_end, dictation_required, reading_required,
                dictation_score, dictation_pass, gate_status, created_at
            ) VALUES ('W1', '2026-08-01', '2026-08-07', 1, 0, 86.0, 1, 'WEEKLY_GATE_PASS', '2026-08-07T10:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO reading_attempts(
                attempt_id, material_id, scope, attempt_number, speed_result, pause_result,
                stress_result, overall_pass, created_at
            ) VALUES ('r1', 'dash-m1', 'PART', 1, 'PASS', 'FAIL', 'PASS', 0, '2026-08-01T10:00:00+00:00'),
                     ('r2', 'dash-m1', 'PART', 2, 'CLOSE', 'PASS', 'PASS', 0, '2026-08-02T10:00:00+00:00')
            """
        )
    service = DashboardService(db)
    result = service.read(scope_id="default")

    weekly = result["trend"]["weekly_dictation"]
    assert weekly[0]["value"] == 86.0
    assert weekly[0]["gate_status"] == "WEEKLY_GATE_PASS"

    reading = result["trend"]["reading_practice"]
    assert reading["dimension_distributions"]["speed"] == {"PASS": 1, "CLOSE": 1}
    assert reading["dimension_distributions"]["pause"] == {"FAIL": 1, "PASS": 1}
    assert reading["dimension_distributions"]["stress"] == {"PASS": 2}

    streak = result["trend"]["difficulty_streak"]
    assert streak["current_stage"] == "STAGE_1"
    assert streak["source"] == "P1 weekly_gate_records"
