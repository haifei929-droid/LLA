"""Dictation context endpoint and Reveal semantics (M1).

The dictation screen must never receive the transcript through the API: the
context payload carries positions and exact flags only, and the full sentence
text is returned solely after an explicit Reveal.
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


def _create(client: TestClient, material_id: str = "ctx-m1") -> None:
    response = client.post(
        "/api/materials",
        json={
            "material_id": material_id,
            "title": "Context material",
            "audio_path": "ctx.wav",
            "transcript": " ".join(DEFAULT_SENTENCES),
            "timestamped_sentences": [
                {"text": text, "start_time": index * 4.0, "end_time": (index + 1) * 4.0}
                for index, text in enumerate(DEFAULT_SENTENCES)
            ],
        },
    )
    assert response.status_code == 201, response.text


def _drive(client: TestClient, material_id: str) -> None:
    client.post(f"/api/materials/{material_id}/first-listen/complete")
    response = client.post(
        f"/api/materials/{material_id}/comprehension-check",
        json={"phase": "FIRST", "self_rating": "30\u201350%", "summary": "Context test."},
    )
    assert response.status_code == 200, response.text


def test_dictation_context_has_no_text_and_marks_exact(client: TestClient) -> None:
    _create(client)
    _drive(client, "ctx-m1")

    context = client.get("/api/materials/ctx-m1/dictation-context").json()
    assert context["current_state"] == "DICTATION_PART_1"
    assert context["part_no"] == 1
    assert len(context["sentences"]) == 3
    assert all("text" not in sentence for sentence in context["sentences"])
    assert all(not sentence["is_exact"] for sentence in context["sentences"])
    first_id = context["sentences"][0]["sentence_id"]
    assert context["current_sentence_id"] is None

    submitted = client.post(
        f"/api/materials/ctx-m1/sentences/{first_id}/dictation",
        json={"user_text": DEFAULT_SENTENCES[0], "listen_count": 1},
    )
    assert submitted.status_code == 200
    assert submitted.json()["expected_text"] is None

    refreshed = client.get("/api/materials/ctx-m1/dictation-context").json()
    assert refreshed["sentences"][0]["is_exact"] == 1
    assert not refreshed["sentences"][1]["is_exact"]
    assert refreshed["current_sentence_id"] == first_id


def test_dictation_context_is_locked_outside_dictation_states(client: TestClient) -> None:
    _create(client)
    locked = client.get("/api/materials/ctx-m1/dictation-context")
    assert locked.status_code == 409


def test_reveal_returns_transcript_and_never_marks_correct(client: TestClient) -> None:
    _create(client)
    _drive(client, "ctx-m1")

    context = client.get("/api/materials/ctx-m1/dictation-context").json()
    first_id = context["sentences"][0]["sentence_id"]

    revealed = client.post(
        f"/api/materials/ctx-m1/sentences/{first_id}/dictation",
        json={"user_text": "", "listen_count": 3, "revealed": True},
    )
    assert revealed.status_code == 200
    payload = revealed.json()
    assert payload["is_exact_match"] is False
    assert payload["expected_text"] == DEFAULT_SENTENCES[0]

    refreshed = client.get("/api/materials/ctx-m1/dictation-context").json()
    assert not refreshed["sentences"][0]["is_exact"]
