"""Homepage recommendation ladder tests (P0 Spec 26.2; Homepage UI V1 spec A3-A9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.core.home_recommendation import HomeRecommendationService
from app.db.connection import Database
from tests.fixtures import DEFAULT_SENTENCES, create_material, make_database, make_settings


def _set_state(
    db: Database,
    material_id: str,
    state: str,
    dictation: dict | None = None,
    reading: dict | None = None,
    updated_at: str = "2026-08-28T10:00:00+00:00",
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE training_progress
               SET current_state = ?, dictation_part_status = ?, reading_part_status = ?,
                   updated_at = ?
             WHERE material_id = ?
            """,
            (
                state,
                json.dumps(dictation or {}, sort_keys=True),
                json.dumps(reading or {}, sort_keys=True),
                updated_at,
                material_id,
            ),
        )


def _seed_week(db: Database, week_id: str = "W1", gate: str = "REINFORCEMENT_REQUIRED") -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO weekly_assessments(
                week_id, period_start, period_end, dictation_required, reading_required,
                dictation_score, dictation_pass, reading_dimension_results, gate_status,
                reinforcement_status, created_at
            ) VALUES (?, '2026-08-24', '2026-08-30', 1, 0, 60.0, 0, '{}', ?, 'REINFORCEMENT_REQUIRED', '2026-08-28T10:00:00+00:00')
            """,
            (week_id, gate),
        )


def test_r7_empty_library(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R7"
    assert result["cta"] == "导入素材"
    assert result["target_view"] == "materials"
    assert result["tone"] == "default"


def test_r2_resume_dictation_part(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "DICTATION_PART_2", dictation={"1": True, "2": False, "3": False})
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R2"
    assert result["title"] == "继续听写 Part 2"
    assert result["material_id"] == "m1"
    assert result["target_view"] == "training"
    assert result["tone"] == "default"


def test_r1_reinforcement_beats_dictation(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "DICTATION_PART_1")
    _seed_week(db, gate="REINFORCEMENT_REQUIRED")
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R1"
    assert result["cta"] == "进入强化训练"
    assert result["week_id"] == "W1"
    assert result["tone"] == "danger"


def test_r1_unfinished_test_beats_dictation(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "DICTATION_PART_1")
    _seed_week(db, gate="DICTATION_WEEKLY_TEST")
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R1"
    assert result["cta"] == "继续周测"


def test_r3_second_listen_and_retest(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "SECOND_FULL_LISTEN")
    assert HomeRecommendationService(db).read()["title"] == "继续二次复听"
    _set_state(db, "m1", "SECOND_COMPREHENSION_CHECK")
    assert HomeRecommendationService(db).read()["title"] == "继续理解复测"
    for result in (HomeRecommendationService(db).read(),):
        assert result["priority"] == "R3"
        assert result["material_id"] == "m1"


def test_r4_next_reading_part(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "READING_AVAILABLE", reading={"1": True, "2": False, "3": False})
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R4"
    assert result["title"] == "继续朗读 Part 2"


def test_r5_full_reading_assessment(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "FULL_READING_ASSESSMENT", reading={"1": True, "2": True, "3": True})
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R5"
    assert result["cta"] == "开始全文验收"


def test_r6_completed_material_and_r2_priority(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "done-1")
    create_material(db, "busy-1")
    _set_state(db, "done-1", "FULLY_COMPLETED", updated_at="2026-08-27T10:00:00+00:00")
    _set_state(db, "busy-1", "DICTATION_PART_3", dictation={"1": True, "2": True, "3": False},
               updated_at="2026-08-28T10:00:00+00:00")
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R2"
    assert result["material_id"] == "busy-1"

    # Every material completed -> R0 (no recommendation), gate or not.
    _set_state(db, "busy-1", "FULLY_COMPLETED")
    result = HomeRecommendationService(db).read()
    assert result["priority"] is None

    # A stale in-progress material older than the completed one -> R6.
    create_material(db, "stale-1")
    _set_state(db, "stale-1", "READY_FIRST_LISTEN", updated_at="2026-08-26T10:00:00+00:00")
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R6"
    assert result["cta"] == "获取下一篇素材"
    assert result["material_id"] == "busy-1"


def test_r0_all_complete_gate_passed(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    _set_state(db, "m1", "FULLY_COMPLETED")
    _seed_week(db, gate="WEEKLY_GATE_PASS")
    result = HomeRecommendationService(db).read()
    assert result["priority"] is None
    assert result["cta"] is None


def test_r_continue_for_blind_listen(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    # A freshly imported material sits in READY_FIRST_LISTEN.
    result = HomeRecommendationService(db).read()
    assert result["priority"] == "R_CONTINUE"
    assert result["cta"] == "继续训练"
    assert result["material_id"] == "m1"


def test_read_only_no_side_effects(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    create_material(db, "m2")
    _set_state(db, "m1", "DICTATION_PART_1")
    _set_state(db, "m2", "READING_AVAILABLE")
    service = HomeRecommendationService(db)
    first = service.read()
    second = service.read()
    assert first == second
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM training_progress").fetchone()[0] == 2


def test_api_recommendation_endpoint(tmp_path: Path) -> None:
    audio = tmp_path / "data" / "materials" / "home.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"RIFFfake")
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            empty = client.get("/api/home/recommendation")
            assert empty.status_code == 200, empty.text
            assert empty.json()["priority"] == "R7"

            created = client.post(
                "/api/materials",
                json={
                    "material_id": "home-001",
                    "title": "Homepage material",
                    "audio_path": str(audio),
                    "transcript": " ".join(DEFAULT_SENTENCES),
                    "timestamped_sentences": [
                        {"text": text, "start_time": index * 4.0, "end_time": (index + 1) * 4.0}
                        for index, text in enumerate(DEFAULT_SENTENCES)
                    ],
                },
            )
            assert created.status_code == 201, created.text
            resumed = client.get("/api/home/recommendation").json()
            assert resumed["priority"] == "R_CONTINUE"
            assert resumed["material_id"] == "home-001"

            advanced = client.post("/api/materials/home-001/first-listen/complete")
            assert advanced.status_code == 200
            assert client.get("/api/home/recommendation").json()["title"] == "继续盲听与理解检查"
    finally:
        main_module.settings = original_settings
