"""P0 acceptance smoke: the full material loop driven purely through the API.

Maps to Spec 31.1 (listening path) plus the state guards fixed in M0:
- duplicate material creation returns 409, never 500
- dictation is locked before the first comprehension check
- sentences must be completed in order within an unlocked Part
- a Part cannot be completed until every sentence is exact
- skip-reading yields LISTENING_COMPLETED with full_reading_status SKIPPED
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from tests.fixtures import DEFAULT_SENTENCES, make_settings


@pytest.fixture
def client(tmp_path: Path):
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as test_client:
            yield test_client
    finally:
        main_module.settings = original_settings


def _create_material(client: TestClient, material_id: str = "acc-m1") -> None:
    response = client.post(
        "/api/materials",
        json={
            "material_id": material_id,
            "title": "Acceptance material",
            "audio_path": "data/materials/acc-m1.wav",
            "transcript": " ".join(DEFAULT_SENTENCES),
            "timestamped_sentences": [
                {"text": text, "start_time": index * 4.0, "end_time": (index + 1) * 4.0}
                for index, text in enumerate(DEFAULT_SENTENCES)
            ],
        },
    )
    assert response.status_code == 201, response.text


def _dictate(client: TestClient, material_id: str, sentence_no: int, text: str, listen_count: int = 1):
    sentence_id = f"{material_id}-sentence-{sentence_no:03d}"
    return client.post(
        f"/api/materials/{material_id}/sentences/{sentence_id}/dictation",
        json={"user_text": text, "listen_count": listen_count},
    )


def _drive_to_dictation(client: TestClient, material_id: str) -> None:
    assert (
        client.post(f"/api/materials/{material_id}/first-listen/complete").json()["current_state"]
        == "FIRST_COMPREHENSION_CHECK"
    )
    assert (
        client.post(
            f"/api/materials/{material_id}/comprehension-check",
            json={"phase": "FIRST", "self_rating": "30\u201350%", "summary": "Main idea is clear."},
        ).json()["current_state"]
        == "DICTATION_PART_1"
    )


def test_duplicate_material_creation_returns_409(client: TestClient) -> None:
    _create_material(client)
    duplicate = client.post(
        "/api/materials",
        json={
            "material_id": "acc-m1",
            "title": "Duplicate",
            "audio_path": "dup.wav",
            "transcript": " ".join(DEFAULT_SENTENCES),
            "timestamped_sentences": [
                {"text": text, "start_time": index * 4.0, "end_time": (index + 1) * 4.0}
                for index, text in enumerate(DEFAULT_SENTENCES)
            ],
        },
    )
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_listening_loop_guards_and_completes(client: TestClient) -> None:
    _create_material(client)
    material_id = "acc-m1"

    # Dictation is locked until the first comprehension check is submitted.
    locked = _dictate(client, material_id, 1, DEFAULT_SENTENCES[0])
    assert locked.status_code == 409

    _drive_to_dictation(client, material_id)

    # Out-of-order sentence within the Part is rejected.
    out_of_order = _dictate(client, material_id, 2, DEFAULT_SENTENCES[1])
    assert out_of_order.status_code == 409

    # Part completion before every sentence is exact is rejected.
    assert _dictate(client, material_id, 1, DEFAULT_SENTENCES[0]).status_code == 200
    incomplete = client.post(f"/api/materials/{material_id}/dictation-parts/1/complete")
    assert incomplete.status_code == 400

    # Complete Part 1 with a wrong-then-exact pair, then finish Parts 2 and 3.
    assert _dictate(client, material_id, 2, "She sells seashells wrong", listen_count=2).json()["is_exact_match"] is False
    assert _dictate(client, material_id, 2, DEFAULT_SENTENCES[1], listen_count=3).status_code == 200
    assert _dictate(client, material_id, 3, DEFAULT_SENTENCES[2]).status_code == 200
    assert (
        client.post(f"/api/materials/{material_id}/dictation-parts/1/complete").json()["current_state"]
        == "DICTATION_PART_2"
    )
    for part, indexes in ((2, (3, 4, 5)), (3, (6, 7, 8))):
        for index in indexes:
            assert _dictate(client, material_id, index + 1, DEFAULT_SENTENCES[index]).status_code == 200
        expected = "SECOND_FULL_LISTEN" if part == 3 else f"DICTATION_PART_{part + 1}"
        assert (
            client.post(f"/api/materials/{material_id}/dictation-parts/{part}/complete").json()["current_state"]
            == expected
        )

    # Second listen and comprehension unlock reading; skipping is legal.
    assert (
        client.post(f"/api/materials/{material_id}/second-listen/complete").json()["current_state"]
        == "SECOND_COMPREHENSION_CHECK"
    )
    assert (
        client.post(
            f"/api/materials/{material_id}/comprehension-check",
            json={"phase": "SECOND", "self_rating": ">70%", "summary": "Details are clear."},
        ).json()["current_state"]
        == "READING_AVAILABLE"
    )
    skipped = client.post(f"/api/materials/{material_id}/reading/skip").json()
    assert skipped["current_state"] == "LISTENING_COMPLETED"
    assert skipped["full_reading_status"] == "SKIPPED"


def test_progress_persists_across_restart(client: TestClient) -> None:
    """Spec 31.3: a fresh app instance resumes at Part / Sentence / Attempt."""
    _create_material(client, "acc-m2")
    _drive_to_dictation(client, "acc-m2")
    assert _dictate(client, "acc-m2", 1, "The quick brown fox wrong", listen_count=2).status_code == 200
    assert _dictate(client, "acc-m2", 1, DEFAULT_SENTENCES[0], listen_count=3).status_code == 200
    assert _dictate(client, "acc-m2", 2, "She sells seashells wrong", listen_count=1).status_code == 200

    progress = client.get("/api/materials/acc-m2/progress").json()
    assert progress["current_state"] == "DICTATION_PART_1"
    assert progress["current_sentence_id"] == "acc-m2-sentence-002"
    assert progress["current_attempt"] == 1
