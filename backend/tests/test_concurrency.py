"""Concurrency and optimistic-lock behavior of dictation submissions.

Spec 34: state changes must be traceable and must not silently overwrite each
other. A concurrent submit must never yield 500 or lose an attempt.
"""

from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app import main as main_module
from tests.fixtures import DEFAULT_SENTENCES, create_material, make_database, make_settings


def test_concurrent_dictation_submits_never_500(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    create_material(database, "m1")
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    results: list[int] = []
    lock = threading.Lock()

    def worker(client: TestClient) -> None:
        # Both submissions are wrong on purpose: neither reaches exact, so the
        # "sentence already complete" guard must not reject them. The only
        # failure mode under test is a race in attempt numbering (UNIQUE
        # violation -> 500) or a lost progress update.
        response = client.post(
            "/api/materials/m1/sentences/m1-sentence-001/dictation",
            json={"user_text": "wrong wrong wrong", "listen_count": 1, "operation_id": f"op-{uuid4().hex}"},
        )
        with lock:
            results.append(response.status_code)

    try:
        with TestClient(main_module.app) as client:
            client.post("/api/materials/m1/first-listen/complete")
            client.post(
                "/api/materials/m1/comprehension-check",
                json={"phase": "FIRST", "self_rating": "30\u201350%", "summary": "Concurrent test."},
            )
            threads = [
                threading.Thread(target=worker, args=(client,))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
    finally:
        main_module.settings = original_settings

    assert 500 not in results, f"concurrent submits produced 500: {results}"
    assert results == [200, 200], f"both submissions should be accepted: {results}"

    with database.connect() as connection:
        attempts = connection.execute(
            "SELECT attempt_number FROM dictation_attempts WHERE sentence_id = 'm1-sentence-001' ORDER BY attempt_number"
        ).fetchall()
        progress = connection.execute(
            "SELECT current_attempt, version FROM training_progress WHERE material_id = 'm1'"
        ).fetchone()
    assert [row["attempt_number"] for row in attempts] == [1, 2]
    assert progress["current_attempt"] == 2


def test_progress_version_bump_is_traceable(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    create_material(database, "m1")
    with database.connect() as connection:
        connection.execute(
            "UPDATE training_progress SET current_state = 'DICTATION_PART_1' WHERE material_id = 'm1'"
        )
        before = connection.execute(
            "SELECT version FROM training_progress WHERE material_id = 'm1'"
        ).fetchone()["version"]
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            response = client.post(
                "/api/materials/m1/sentences/m1-sentence-001/dictation",
                json={"user_text": DEFAULT_SENTENCES[0], "listen_count": 1, "operation_id": f"op-{uuid4().hex}"},
            )
            assert response.status_code == 200, response.text
    finally:
        main_module.settings = original_settings
    with database.connect() as connection:
        after = connection.execute(
            "SELECT version FROM training_progress WHERE material_id = 'm1'"
        ).fetchone()["version"]
    assert after == before + 1
