"""Material intake API: upload, transcript, alignment, approval, acquisition.

The behaviours pinned here are the ones the closed loop depends on and that are
easy to get wrong: a staged file is never trainable by itself, an approval made
from stale or missing evidence is refused server-side, and re-uploading the same
bytes never duplicates a material.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app import main as main_module
from tests.fixtures import make_settings, make_sine_wav


@pytest.fixture()
def client(tmp_path: Path):
    settings = make_settings(tmp_path)
    original = main_module.settings
    main_module.settings = settings
    routes_module.settings = settings
    try:
        with TestClient(main_module.app) as test_client:
            yield test_client, settings
    finally:
        main_module.settings = original


def _upload(client: TestClient, tmp_path: Path, name: str = "lesson.wav", **params) -> dict:
    wav = make_sine_wav(tmp_path / "source" / name, segments=[(6.0, 9000.0)] * 6)
    response = client.post(
        "/api/materials/intake/upload",
        params={"filename": name, **params},
        content=wav.read_bytes(),
        headers={"content-type": "audio/wav"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _transcript(n: int = 6) -> str:
    return "\n".join(f"This is sentence number {index} of the lesson." for index in range(1, n + 1))


def _write_alignment(item: dict, settings, *, unaligned: int = 0, stale: bool = False, coverage: float = 1.0) -> Path:
    batch_dir = Path(settings.project_root) / "data" / "import" / "inbox" / item["batch"]
    audio = batch_dir / item["audio"]
    transcript = batch_dir / f"{item['stem']}.txt"
    # Six-second phrases with long pauses: a slow, clearly A1-shaped timeline.
    lines = [
        {"text": line, "start": 6.0 * index, "end": 6.0 * index + 4.5, "repeats": 1, "coverage": 1.0}
        for index, line in enumerate(transcript.read_text(encoding="utf-8").splitlines())
    ]
    payload = {
        "audio": audio.name,
        "model": "test",
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest() if not stale else "0" * 64,
        "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        "lines": lines,
        "unaligned": [{"line": 0, "text": "x", "coverage": 0.1}] * unaligned,
        "coverage": coverage,
    }
    target = batch_dir / f"{item['stem']}.aligned.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_static_intake_route_is_not_shadowed_by_material_id(client) -> None:
    test_client, _settings = client
    response = test_client.get("/api/materials/intake/items")
    assert response.status_code == 200, response.text
    assert "counts" in response.json()


def test_upload_is_controlled_and_not_trainable(client, tmp_path: Path) -> None:
    test_client, settings = client
    item = _upload(test_client, tmp_path, "lesson.wav", title="My lesson")

    assert item["status"] == "AUDIO_ONLY"
    assert item["can_approve"] is False and item["can_train"] is False
    assert "TRANSCRIPT_MISSING" in item["blockers"]
    staged = Path(settings.project_root) / "data" / "import" / "inbox" / "uploads"
    assert (staged / item["audio"]).is_file()
    assert item["audio"].endswith(".wav")
    # Nothing was written to the library.
    listing = test_client.get("/api/materials/intake/items").json()
    assert listing["counts"]["audio_files"] == 1
    assert listing["counts"]["ingested"] == 0


def test_upload_rejects_unsupported_and_empty_files(client, tmp_path: Path) -> None:
    test_client, _settings = client
    bad = test_client.post(
        "/api/materials/intake/upload",
        params={"filename": "notes.txt"},
        content=b"hello",
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "UNSUPPORTED_AUDIO"

    empty = test_client.post(
        "/api/materials/intake/upload",
        params={"filename": "silent.wav"},
        content=b"",
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["code"] == "EMPTY_UPLOAD"


def test_upload_is_idempotent_for_identical_bytes(client, tmp_path: Path) -> None:
    test_client, settings = client
    first = _upload(test_client, tmp_path, "same.wav")
    second = _upload(test_client, tmp_path, "same.wav")

    assert first["item_id"] == second["item_id"]
    assert second["created"] is False
    staged = list((Path(settings.project_root) / "data" / "import" / "inbox" / "uploads").glob("*.wav"))
    assert len(staged) == 1, "identical uploads must not accumulate copies"


def test_transcript_edit_invalidates_alignment(client, tmp_path: Path) -> None:
    test_client, settings = client
    item = _upload(test_client, tmp_path)
    saved = test_client.put(
        f"/api/materials/intake/items/{item['item_id']}/transcript",
        json={"text": _transcript()},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["alignment"]["present"] is False

    _write_alignment(item, settings)
    refreshed = test_client.get("/api/materials/intake/items").json()["items"][0]
    assert refreshed["alignment"]["usable"] is True

    again = test_client.put(
        f"/api/materials/intake/items/{item['item_id']}/transcript",
        json={"text": _transcript(5)},
    )
    assert again.json()["alignment"]["present"] is False, "old evidence must not survive an edit"
    assert again.json()["transcript_text"].splitlines() == _transcript(5).splitlines(), "the editor must be able to show the saved text"


def test_approval_refuses_missing_or_stale_evidence(client, tmp_path: Path) -> None:
    test_client, settings = client
    item = _upload(test_client, tmp_path)

    no_transcript = test_client.post(
        f"/api/materials/intake/items/{item['item_id']}/approve",
        json={"accent": "en-GB", "license_status": "CLEARED", "transcript_verified": True,
              "quality_ok": True, "confirm": True},
    )
    assert no_transcript.status_code == 409
    assert no_transcript.json()["detail"]["code"] == "TRANSCRIPT_MISSING"

    test_client.put(f"/api/materials/intake/items/{item['item_id']}/transcript", json={"text": _transcript()})
    no_alignment = test_client.post(
        f"/api/materials/intake/items/{item['item_id']}/approve",
        json={"accent": "en-GB", "license_status": "CLEARED", "transcript_verified": True,
              "quality_ok": True, "confirm": True},
    )
    assert no_alignment.json()["detail"]["code"] == "ALIGNMENT_MISSING"

    _write_alignment(item, settings, stale=True)
    stale = test_client.post(
        f"/api/materials/intake/items/{item['item_id']}/approve",
        json={"accent": "en-GB", "license_status": "CLEARED", "transcript_verified": True,
              "quality_ok": True, "confirm": True},
    )
    assert stale.json()["detail"]["code"] == "ALIGNMENT_STALE"


def test_approval_rejects_bad_attestations(client, tmp_path: Path) -> None:
    test_client, settings = client
    item = _upload(test_client, tmp_path)
    test_client.put(f"/api/materials/intake/items/{item['item_id']}/transcript", json={"text": _transcript()})
    _write_alignment(item, settings)

    base = {"accent": "en-GB", "license_status": "CLEARED", "transcript_verified": True,
            "quality_ok": True, "confirm": True}
    url = f"/api/materials/intake/items/{item['item_id']}/approve"

    for override, expected, status in (
        ({"confirm": False}, "CONFIRMATION_REQUIRED", 400),
        ({"accent": "en-US"}, "ACCENT_NOT_ELIGIBLE", 409),
        ({"accent": "en-AU"}, "ACCENT_NOT_ELIGIBLE", 409),
        ({"license_status": "UNKNOWN"}, "LICENSE_UNVERIFIED", 409),
        ({"transcript_verified": False}, "TRANSCRIPT_UNVERIFIED", 409),
        ({"quality_ok": False}, "QUALITY_REJECTED", 409),
    ):
        response = test_client.post(url, json={**base, **override})
        assert response.status_code == status, (override, response.text)
        assert response.json()["detail"]["code"] == expected


def test_approval_ingests_and_exposes_pool_eligibility(client, tmp_path: Path) -> None:
    test_client, settings = client
    item = _upload(test_client, tmp_path)
    test_client.put(f"/api/materials/intake/items/{item['item_id']}/transcript", json={"text": _transcript()})
    _write_alignment(item, settings)

    approved = test_client.post(
        f"/api/materials/intake/items/{item['item_id']}/approve",
        json={"accent": "en-GB", "license_status": "CLEARED", "transcript_verified": True,
              "quality_ok": True, "confirm": True},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["ingested"] is True
    assert body["accent"] == "en-GB"
    assert body["license_status"] == "CLEARED"

    # Approval is not band eligibility: with the confirmed stage at A1 the A2
    # material is ingested but not selectable, which is exactly the rule.
    pool = test_client.get("/api/p1/pool").json()
    assert pool["band"] == "A1"
    assert pool["selectable_in_band"] == 0, "an approved A2 material must not leak into the A1 pool"
    assert pool["ingested_by_band"].get(body["cefr_level"]) == 1

    with main_module.app.state.database.connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO training_difficulty_profiles(scope_id, current_stage, updated_at)"
            " VALUES ('default', 'STAGE_2', datetime('now'))"
        )
    uplifted = test_client.get("/api/p1/pool").json()
    assert uplifted["band"] == "A2"
    assert uplifted["selectable_in_band"] == 1

    again = test_client.post(
        f"/api/materials/intake/items/{item['item_id']}/approve",
        json={"accent": "en-GB", "license_status": "CLEARED", "transcript_verified": True,
              "quality_ok": True, "confirm": True},
    )
    assert again.status_code == 200
    assert again.json().get("already_approved") is True
    listing = test_client.get("/api/materials/intake/items").json()
    assert listing["counts"]["ingested"] == 1, "approval must be idempotent"


def test_fetch_and_activate_require_governance(client) -> None:
    test_client, _settings = client
    fetch = test_client.post("/api/materials/intake/fetch-official")
    assert fetch.status_code == 409
    assert fetch.json()["detail"]["code"] == "SOURCE_NOT_APPROVED"

    missing_confirm = test_client.post(
        "/api/materials/intake/activate-source",
        json={"platform": "50LANGUAGES", "license_reference": "x", "catalog_scope": "y", "confirm": False},
    )
    assert missing_confirm.status_code == 400
    assert missing_confirm.json()["detail"]["code"] == "CONFIRMATION_REQUIRED"

    activated = test_client.post(
        "/api/materials/intake/activate-source",
        json={"platform": "50LANGUAGES", "license_reference": "BY-NC-ND-3.0-US",
              "catalog_scope": "book2-en-uk", "confirm": True},
    )
    assert activated.status_code == 200, activated.text
    assert test_client.get("/api/materials/intake/items").json()["active_source"] == "50LANGUAGES"

def test_alignment_refuses_to_download_a_missing_model(client, tmp_path: Path, monkeypatch) -> None:
    from app.core import material_intake

    test_client, _settings = client
    item = _upload(test_client, tmp_path)
    test_client.put(f"/api/materials/intake/items/{item['item_id']}/transcript", json={"text": _transcript()})

    monkeypatch.setattr(material_intake.MaterialIntakeService, "_model_cached", staticmethod(lambda _model: False))
    response = test_client.post(
        f"/api/materials/intake/items/{item['item_id']}/align", json={"model": "small"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MODEL_NOT_AVAILABLE"
    assert "will not download it silently" in response.json()["detail"]["message"]


def test_alignment_requires_a_transcript(client, tmp_path: Path) -> None:
    test_client, _settings = client
    item = _upload(test_client, tmp_path)
    response = test_client.post(f"/api/materials/intake/items/{item['item_id']}/align", json={"model": "small"})
    assert response.status_code in (404, 409)
    assert response.json()["detail"]["code"] == "TRANSCRIPT_MISSING"
