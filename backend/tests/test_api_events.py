from pathlib import Path

from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings


def test_event_api_drives_and_guards_first_dictation_part(tmp_path: Path) -> None:
    original_settings = main_module.settings
    main_module.settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )
    payload = {
        "material_id": "api-m1",
        "title": "API material",
        "audio_path": "api-m1.wav",
        "transcript": "One. Two. Three. Four. Five. Six. Seven. Eight. Nine.",
        "timestamped_sentences": [
            {"text": f"Sentence {index}.", "start_time": (index - 1) * 10, "end_time": index * 10}
            for index in range(1, 10)
        ],
    }
    (tmp_path / "api-m1.wav").write_bytes(b"RIFFdemo")
    try:
        with TestClient(main_module.app) as client:
            response = client.post("/api/materials", json=payload)
            assert response.status_code == 201
            search = client.get("/api/materials/search", params={"q": "API material"})
            assert search.status_code == 200
            assert search.json()[0]["material_id"] == "api-m1"
            detail = client.get("/api/materials/api-m1")
            assert detail.status_code == 200
            assert len(detail.json()["sentences"]) == 9
            audio = client.get("/api/materials/api-m1/audio")
            assert audio.status_code == 200
            assert client.post("/api/materials/api-m1/first-listen/complete").json()["current_state"] == "FIRST_COMPREHENSION_CHECK"
            assert client.post(
                "/api/materials/api-m1/comprehension-check",
                json={"phase": "FIRST", "self_rating": "30–50%", "summary": "Main idea"},
            ).json()["current_state"] == "DICTATION_PART_1"

            out_of_order = client.post(
                "/api/materials/api-m1/sentences/api-m1-sentence-002/dictation",
                json={"user_text": "Sentence 2.", "listen_count": 1},
            )
            assert out_of_order.status_code == 409

            first = client.post(
                "/api/materials/api-m1/sentences/api-m1-sentence-001/dictation",
                json={"user_text": "Sentence 1.", "listen_count": 1},
            )
            assert first.status_code == 200
            incomplete = client.post("/api/materials/api-m1/dictation-parts/1/complete")
            assert incomplete.status_code == 400

            started = client.post(
                "/api/time-logs/start",
                json={"activity_type": "DICTATION", "material_id": "api-m1", "session_id": "s1"},
            )
            assert started.status_code == 200
            time_log_id = started.json()["time_log_id"]
            assert client.post(
                f"/api/time-logs/{time_log_id}/stop", json={"active_seconds": 30}
            ).json()["active_seconds"] == 30
            assert client.get("/api/stats").json()["total_learning_seconds"] == 30

            weekly = client.post(
                "/api/weekly-assessments",
                json={
                    "week_id": "api-week",
                    "period_start": "2026-08-24",
                    "period_end": "2026-08-30",
                },
            )
            assert weekly.status_code == 200
            client.post(
                "/api/weekly-assessments/api-week/dictation",
                json={"score": 90, "passed": True},
            )
            passed = client.post(
                "/api/weekly-assessments/api-week/reading",
                json={"dimensions": {"speed": True, "pause": True, "stress": True}},
            )
            assert passed.json()["gate_status"] == "WEEKLY_GATE_PASS"
    finally:
        main_module.settings = original_settings
