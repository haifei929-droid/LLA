"""P1 candidate pipeline: search -> quality grades -> transcript validation
-> rank -> selection -> prepare (idempotent, recoverable)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.adapters.speech import RecognizedSegment
from app.core.audio_quality import AudioQualityAnalyzer, QualityThresholds
from app.core.material_candidates import MaterialCandidateService
from app.core.material_preparation import (
    CANDIDATE_EXPIRED,
    CANDIDATE_NOT_SELECTABLE,
    IDEMPOTENCY_CONFLICT,
    MaterialSelectionError,
    MaterialPreparationService,
)
from app.db.connection import Database
from tests.fixtures import make_database, make_settings, make_sine_wav

# Test uses a 1-2 minute band so synthetic WAVs are cheap; the production
# default of 15-20 minutes stays in config.
DUR_MIN, DUR_MAX = 1.0, 2.0


class FakeVOADeepProvider:
    """Provider that mimics the P1 VOA surface: entries with RSS durations."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.entries: list[tuple[str, str, str, float | None]] = []
        self.downloaded: list[str] = []

    def _list_episodes(self, zone_ids=None) -> list[tuple[str, str, str, float | None]]:
        return self.entries

    def download_audio(self, mp3_url: str, audio_id: str, work_dir: Path) -> Path:
        self.downloaded.append(audio_id)
        # Entry-relative: (url, kind) -> wav variant
        kind = "clear" if "clear" in mp3_url else "acceptable" if "acceptable" in mp3_url else "poor"
        wav = work_dir / f"{audio_id}.wav"
        if kind == "clear":
            make_sine_wav(wav, segments=[(65.0, 12000.0), (1.0, 0.0)])
        elif kind == "acceptable":
            # Low SNR: loud noise floor -> Acceptable band.
            make_sine_wav(wav, segments=[(65.0, 12000.0), (60.0, 2000.0)])
        else:
            make_sine_wav(wav, segments=[(5.0, 12000.0)])  # too short -> Poor
        return wav


class FakeASR:
    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        # Deterministic segments spanning the audio length so transcript
        # validation passes coverage checks.
        return [
            RecognizedSegment(text=f"Sample sentence number {index} with enough words here.", start_time=index * 2.0, end_time=index * 2.0 + 1.8)
            for index in range(30)
        ]


def _provider_entries() -> list[tuple[str, str, str, float | None]]:
    return [
        (f"https://voa.example/a/{i}.html", f"https://voa.example/audio-{kind}-{i}.mp3", f"Episode {i} {kind}", duration)
        for i, kind, duration in (
            (1, "clear", 70.0),          # RSS says in-band -> proceeds to real check
            (2, "clear", 5000.0),        # out of band -> rejected by RSS duration
            (3, "acceptable", 80.0),
            (4, "poor", 75.0),
            (5, "clear", None),          # missing duration -> rejected
        )
    ]


def _service(tmp_path: Path) -> tuple[Database, MaterialCandidateService]:
    database = make_database(tmp_path)
    provider = FakeVOADeepProvider(tmp_path)
    provider.entries = _provider_entries()
    service = MaterialCandidateService(
        database,
        make_settings(tmp_path),
        provider=provider,
        asr=FakeASR(),
        quality=AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
    )
    return database, service


def test_search_filters_ranks_and_creates_candidates(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    result = service.search(
        scope_id="default", speed_stage="STAGE_1",
        target_duration_min=DUR_MIN, target_duration_max=DUR_MAX, max_results=3,
    )
    candidates = result["candidates"]
    # RSS-out-of-band (5000s), missing duration and Poor never reach candidates.
    assert len(candidates) <= 3
    assert all(c["audio_quality"] in ("Clear", "Acceptable") for c in candidates)
    assert all(c["transcript_status"] == "COMPLETE" for c in candidates)
    assert result["rejection_summary"].get("duration_out_of_range", 0) >= 1
    assert result["rejection_summary"].get("duration_missing", 0) >= 1
    assert result["rejection_summary"].get("quality_poor", 0) >= 1
    # Clear ranks before Acceptable.
    if len(candidates) >= 2:
        assert candidates[0]["audio_quality"] == "Clear"

    with database.connect() as connection:
        rows = connection.execute("SELECT COUNT(*) AS n FROM material_candidates").fetchone()
        reports = connection.execute("SELECT COUNT(*) AS n FROM audio_quality_reports").fetchone()
    assert rows["n"] == len(candidates)
    assert reports["n"] == len(candidates)


def test_search_dedupes_by_fingerprint(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    second = service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    assert second["rejection_summary"].get("duplicate", 0) >= 1
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS n FROM material_candidates").fetchone()["n"]
    assert count <= 3


def test_prepare_is_idempotent_and_recoverable(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    result = service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    candidate_id = result["candidates"][0]["candidate_id"]
    prep = MaterialPreparationService(database, make_settings(tmp_path))

    first = prep.prepare(candidate_id, "default", "key-1")
    assert first["prepare_status"] == "READY"
    assert first["reused"] is False
    material_id = first["material_id"]

    # Same key -> same material, no duplicate.
    again = prep.prepare(candidate_id, "default", "key-1")
    assert again["material_id"] == material_id
    assert again["reused"] is True
    # Different key on an already-selected candidate -> conflict.
    with pytest.raises(MaterialSelectionError) as exc:
        prep.prepare(candidate_id, "default", "key-2")
    assert exc.value.code == IDEMPOTENCY_CONFLICT

    with database.connect() as connection:
        materials = connection.execute("SELECT COUNT(*) AS n FROM materials WHERE source_candidate_id = ?", (candidate_id,)).fetchone()
        assert materials["n"] == 1
        candidate = connection.execute(
            "SELECT candidate_status, speed_stage FROM material_candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        assert candidate["candidate_status"] == "SELECTED"
        assert candidate["speed_stage"] == "STAGE_1"


def test_prepare_rejects_expired_or_poor_candidates(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    result = service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    candidates = result["candidates"]
    prep = MaterialPreparationService(database, make_settings(tmp_path))

    # Poor candidates never appear, so simulate a manually marked Poor one.
    with database.connect() as connection:
        connection.execute(
            "UPDATE material_candidates SET audio_quality = 'Poor' WHERE candidate_id = ?",
            (candidates[0]["candidate_id"],),
        )
    with pytest.raises(MaterialSelectionError) as exc:
        prep.prepare(candidates[0]["candidate_id"], "default", "key-x")
    assert exc.value.code == CANDIDATE_NOT_SELECTABLE

    # Expired candidate.
    with database.connect() as connection:
        connection.execute(
            "UPDATE material_candidates SET audio_quality = 'Clear', expires_at = '2020-01-01T00:00:00+00:00' WHERE candidate_id = ?",
            (candidates[0]["candidate_id"],),
        )
    with pytest.raises(MaterialSelectionError) as exc:
        prep.prepare(candidates[0]["candidate_id"], "default", "key-y")
    assert exc.value.code == CANDIDATE_EXPIRED


def test_api_prepare_round_trip(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    result = service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    candidate_id = result["candidates"][0]["candidate_id"]

    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            prepared = client.post(
                f"/api/p1/material-candidates/{candidate_id}/prepare",
                json={"scope_id": "default", "idempotency_key": "api-key-1"},
            )
            assert prepared.status_code == 200, prepared.text
            assert prepared.json()["prepare_status"] == "READY"
            material_id = prepared.json()["material_id"]

            again = client.post(
                f"/api/p1/material-candidates/{candidate_id}/prepare",
                json={"scope_id": "default", "idempotency_key": "api-key-1"},
            )
            assert again.json()["material_id"] == material_id

            detail = client.post(
                f"/api/p1/material-candidates/{candidate_id}/prepare",
                json={"scope_id": "default", "idempotency_key": "api-key-2"},
            )
            assert detail.status_code == 409
            assert detail.json()["detail"]["code"] == IDEMPOTENCY_CONFLICT
    finally:
        main_module.settings = original_settings
