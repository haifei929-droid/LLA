"""P0 acceptance: the full training loop driven purely through the API.

Covers Spec 31.1 (material main line to FULLY_COMPLETED), the weekly
FAIL -> reinforcement -> retest -> PASS loop (31.6/31.7), cross-day resume
(31.3), and learning-time aggregation (31.8) in one black-box script.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from tests.fixtures import DEFAULT_SENTENCES, make_settings, make_sine_wav

REFERENCE = [(2.0, 12000.0), (0.5, 0.0)]


@pytest.fixture
def client(tmp_path: Path):
    # Seed a material whose audio file exists on disk (reading scoring needs it).
    audio = tmp_path / "data" / "materials" / "accept.wav"
    make_sine_wav(audio, segments=[(2.0, 12000.0), (0.5, 0.0)])
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as test_client:
            response = test_client.post(
                "/api/materials",
                json={
                    "material_id": "acc-001",
                    "title": "Acceptance material",
                    "audio_path": str(audio),
                    "transcript": " ".join(DEFAULT_SENTENCES),
                    "timestamped_sentences": [
                        {"text": text, "start_time": index * 4.0, "end_time": (index + 1) * 4.0}
                        for index, text in enumerate(DEFAULT_SENTENCES)
                    ],
                },
            )
            assert response.status_code == 201, response.text
            yield test_client
    finally:
        main_module.settings = original_settings


def _dictate(client: TestClient, material_id: str, sentence_no: int, text: str, listen_count: int = 1):
    return client.post(
        f"/api/materials/{material_id}/sentences/{material_id}-sentence-{sentence_no:03d}/dictation",
        json={"user_text": text, "listen_count": listen_count},
    )


def _score_reading(client: TestClient, material_id: str, scope: str, part_no, tmp_path: Path, segment=None):
    recording = tmp_path / f"rec-{scope}-{part_no or 'full'}.wav"
    make_sine_wav(recording, segments=segment or [(2.05, 12000.0), (0.5, 0.0)])
    url = (
        f"/api/materials/{material_id}/reading-parts/{part_no}/score"
        if scope == "PART"
        else f"/api/materials/{material_id}/full-reading/score"
    )
    return client.post(
        url,
        json={"filename": recording.name, "content_base64": base64.b64encode(recording.read_bytes()).decode()},
    )


def test_full_material_loop_to_completion(client: TestClient, tmp_path: Path) -> None:
    mid = "acc-001"

    assert client.post(f"/api/materials/{mid}/first-listen/complete").json()["current_state"] == "FIRST_COMPREHENSION_CHECK"
    assert client.post(
        f"/api/materials/{mid}/comprehension-check",
        json={"phase": "FIRST", "self_rating": "30\u201350%", "summary": "Main idea understood."},
    ).json()["current_state"] == "DICTATION_PART_1"

    for part in (1, 2, 3):
        for index in range((part - 1) * 3, part * 3):
            # One wrong attempt, then exact.
            assert _dictate(client, mid, index + 1, "wrong", listen_count=1).json()["is_exact_match"] is False
            assert _dictate(client, mid, index + 1, DEFAULT_SENTENCES[index], listen_count=2).status_code == 200
        response = client.post(f"/api/materials/{mid}/dictation-parts/{part}/complete")
        assert response.status_code == 200, response.text

    assert client.post(f"/api/materials/{mid}/second-listen/complete").json()["current_state"] == "SECOND_COMPREHENSION_CHECK"
    assert client.post(
        f"/api/materials/{mid}/comprehension-check",
        json={"phase": "SECOND", "self_rating": ">70%", "summary": "Details are now clear."},
    ).json()["current_state"] == "READING_AVAILABLE"

    for part in (1, 2, 3):
        scored = _score_reading(client, mid, "PART", part, tmp_path)
        assert scored.status_code == 200, scored.text
        assert scored.json()["overall_pass"] is True, scored.text
        state = client.post(f"/api/materials/{mid}/reading-parts/{part}/complete").json()["current_state"]
        expected = "FULL_READING_ASSESSMENT" if part == 3 else "READING_AVAILABLE"
        assert state == expected

    full = _score_reading(client, mid, "FULL", None, tmp_path)
    assert full.json()["overall_pass"] is True
    completed = client.post(f"/api/materials/{mid}/full-reading-assessment", json={"passed": True})
    assert completed.json()["current_state"] == "FULLY_COMPLETED"


def test_weekly_fail_reinforce_retest_pass(client: TestClient) -> None:
    created = client.post(
        "/api/weekly-assessments",
        json={"week_id": "ACC-W1", "period_start": "2026-08-24", "period_end": "2026-08-30",
              "dictation_required": True, "reading_required": False},
    )
    assert created.status_code == 200, created.text
    items = client.post("/api/weekly-assessments/ACC-W1/test-items", json={"count": 3})
    assert items.status_code == 200, items.text
    items = items.json()

    # Fail the test on purpose: two wrong of three -> ~33% < 80%.
    for item in items:
        response = client.post(
            f"/api/weekly-assessments/ACC-W1/test-items/{item['item_id']}/dictation",
            json={"user_text": "wrong", "listen_count": 1},
        )
        assert response.status_code == 200
    failed = client.get("/api/weekly-assessments/ACC-W1").json()
    assert failed["gate_status"] == "REINFORCEMENT_REQUIRED"
    assert failed["dictation_pass"] is False

    # Reinforcement package from the failed test items; complete it exactly.
    reinforced = client.post("/api/weekly-assessments/ACC-W1/reinforcement/start")
    assert reinforced.status_code == 200, reinforced.text
    items = reinforced.json()["reinforcement_items"]
    assert items, "reinforcement items must exist"
    for item in items:
        response = client.post(
            f"/api/weekly-assessments/ACC-W1/reinforcement/items/{item['item_id']}/dictation",
            json={"user_text": item["text"], "listen_count": 1},
        )
        assert response.status_code == 200, response.text

    # All exact -> targeted retest armed; confirm to recover the gate.
    armed = client.get("/api/weekly-assessments/ACC-W1").json()
    assert armed["gate_status"] == "TARGETED_RETEST"
    recovered = client.post("/api/weekly-assessments/ACC-W1/retest/confirm")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["gate_status"] == "WEEKLY_GATE_PASS"
    assert recovered.json()["reinforcement_status"] == "COMPLETED"


def test_resume_and_time_aggregation(client: TestClient, tmp_path: Path) -> None:
    mid = "acc-001"
    client.post(f"/api/materials/{mid}/first-listen/complete")
    client.post(
        f"/api/materials/{mid}/comprehension-check",
        json={"phase": "FIRST", "self_rating": "30\u201350%", "summary": "resume test."},
    )
    assert _dictate(client, mid, 1, "wrong", listen_count=2).status_code == 200

    # "Restart": a fresh app instance on the same database resumes the spot.
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as restarted:
            progress = restarted.get(f"/api/materials/{mid}/progress").json()
            assert progress["current_state"] == "DICTATION_PART_1"
            assert progress["current_sentence_id"] == f"{mid}-sentence-001"
            assert progress["current_attempt"] == 1
            assert _dictate(restarted, mid, 1, DEFAULT_SENTENCES[0], listen_count=3).json()["is_exact_match"] is True
    finally:
        main_module.settings = original_settings

    # Time logs aggregate only when stopped.
    started = client.post("/api/time-logs/start", json={"activity_type": "DICTATION"})
    assert client.get("/api/stats").json()["total_learning_seconds"] == 0
    client.post(f"/api/time-logs/{started.json()['time_log_id']}/stop", json={"active_seconds": 90})
    assert client.get("/api/stats").json()["total_learning_seconds"] == 90
