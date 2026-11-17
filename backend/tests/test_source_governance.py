"""Source governance: activation, discovery, review, ordering, atomic ingestion.

Covers the acceptance matrix of docs/LLA_Source_Governance_DSH_Plan.md:
single active source with no implicit fallback, en-US exclusion at every
entry, quarantine with reasons (and no transcript leak), strict platform
course order, one formal material per source identity, crash-safe atomic
ingestion, honest completion reports, confirmed-only switching, and a
migration that preserves history without inventing accents.

All tests run against a temporary database and local fixtures only; no network
access, no platform activation and no fixture is presented as real platform
validation.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.adapters.speech import RecognizedSegment
from app.core.audio_quality import AudioQualityAnalyzer, QualityThresholds
from app.core.difficulty_progression import DifficultyProgressionService
from app.core.material_candidates import (
    ELIGIBLE,
    INGESTED,
    PENDING_REVIEW,
    REJECTED,
    RETRYABLE_FAILED,
    MaterialCandidateService,
    SourceGovernanceError,
    assess_disposition,
    normalize_accent,
)
from app.core.material_preparation import MaterialPreparationService, MaterialSelectionError
from app.core.material_search import MaterialSearchService
from app.core.materials import MaterialStore
from app.core.weekly import WeeklyAssessmentService
from app.db.connection import Database
from tests.fixtures import make_database, make_settings, make_sine_wav

NOW = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)
PLATFORM = "50LANGUAGES"
NEXT_PLATFORM = "BRITISH_COUNCIL"

OFFICIAL_TRANSCRIPT = " ".join(
    f"This is the official sentence number {index} of the lesson." for index in range(1, 13)
)


class FakeASR:
    """Deterministic ASR segments that agree with the fixture official script.

    An official transcript that an independent ASR pass contradicts is
    quarantined, so the positive fixtures must be self-consistent; the
    contradiction case has its own negative test.
    """

    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        return [
            RecognizedSegment(
                text=f"This is the official sentence number {index} of the lesson.",
                start_time=(index - 1) * 5.4,
                end_time=(index - 1) * 5.4 + 5.35,
            )
            for index in range(1, 13)
        ]


class FakeGovernedProvider:
    """Offline provider exposing a declared catalog snapshot."""

    def __init__(
        self,
        items: list[dict[str, object]],
        *,
        catalog: dict[str, object] | None = None,
        failing_audio: set[str] | None = None,
        short_audio: set[str] | None = None,
        failing_catalog: bool = False,
    ) -> None:
        self.items = items
        self.catalog = catalog if catalog is not None else {
            "catalog_complete": True,
            "known_total": len(items),
            "has_cursor": False,
            "catalog_scope": "fixture-snapshot",
        }
        self.failing_audio = failing_audio or set()
        self.short_audio = short_audio or set()
        self.failing_catalog = failing_catalog
        self.downloads: list[str] = []

    def catalog_items(self) -> list[dict[str, object]]:
        if self.failing_catalog:
            raise RuntimeError("catalog endpoint unavailable")
        return list(self.items)

    def catalog_info(self) -> dict[str, object]:
        return dict(self.catalog)

    def download_audio(self, mp3_url: str, audio_id: str, work_dir: Path) -> Path:
        self.downloads.append(audio_id)
        if mp3_url in self.failing_audio:
            raise RuntimeError("transient download failure")
        wav = work_dir / f"{audio_id}.wav"
        if mp3_url in self.short_audio:
            make_sine_wav(wav, segments=[(5.0, 12000.0)])
        else:
            make_sine_wav(wav, segments=[(65.0, 12000.0), (1.0, 0.0)])
        return wav


def item(
    item_id: str,
    *,
    accent: str | None = "en-GB",
    cefr_level: str | None = "A1",
    course: str | None = "course-a",
    course_order: int | None = 1,
    item_order: int | None = 1,
    license_status: str = "CLEARED",
    transcript_verified: bool = True,
    transcript_origin: str | None = "OFFICIAL",
    duration: float | None = 66.0,
    source_url: str | None = None,
) -> dict[str, object]:
    return {
        "source_item_id": item_id,
        "title": f"Lesson {item_id}",
        "source_url": source_url or f"https://source.example/lessons/{item_id}.html",
        "audio_url": f"https://source.example/audio/{item_id}.mp3",
        "duration_seconds": duration,
        "source_course": course,
        "course_order": course_order,
        "item_order": item_order,
        "cefr_level": cefr_level,
        "accent": accent,
        "speaker_id": "speaker-a",
        "transcript": OFFICIAL_TRANSCRIPT,
        "transcript_origin": transcript_origin,
        "transcript_verified": transcript_verified,
        "license_status": license_status,
        "license_reference": "fixture-license",
        "accent_evidence": "human checked",
        "transcript_evidence": "publisher transcript",
    }


def _service(
    tmp_path: Path,
    provider: FakeGovernedProvider,
    *,
    database: Database | None = None,
    quality: AudioQualityAnalyzer | None = None,
) -> tuple[Database, MaterialCandidateService]:
    database = database or make_database(tmp_path)
    service = MaterialCandidateService(
        database,
        make_settings(tmp_path),
        provider=provider,
        asr=FakeASR(),
        quality=quality or AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
        now_fn=lambda: NOW,
    )
    return database, service


def _activate(service: MaterialCandidateService, platform: str = PLATFORM) -> dict[str, object]:
    return service.activate_source(
        "default",
        platform,
        license_reference="fixture-license",
        catalog_scope="fixture-snapshot",
        confirmed_by="TEST",
    )


def _preparation(database: Database, tmp_path: Path) -> MaterialPreparationService:
    return MaterialPreparationService(
        database,
        make_settings(tmp_path),
        now_fn=lambda: NOW,
        candidates=MaterialCandidateService(
            database, make_settings(tmp_path), now_fn=lambda: NOW
        ),
    )


# ---------------------------------------------------------------------------
# 1. Single active source, no implicit fallback
# ---------------------------------------------------------------------------

def test_activation_requires_evidence_and_blocks_implicit_sources(tmp_path: Path) -> None:
    _, service = _service(tmp_path, FakeGovernedProvider([item("a1")]))

    with pytest.raises(SourceGovernanceError) as exc:
        service.search(scope_id="default")
    assert exc.value.code == "SOURCE_NOT_APPROVED"

    with pytest.raises(SourceGovernanceError) as exc:
        service.activate_source("default", "SOME_RANDOM_SITE", "license", "scope")
    assert exc.value.code == "UNKNOWN_SOURCE"

    with pytest.raises(SourceGovernanceError) as exc:
        service.activate_source("default", PLATFORM, "  ", "scope")
    assert exc.value.code == "SOURCE_NOT_APPROVED"

    activated = _activate(service)
    assert activated["platform"] == PLATFORM
    assert activated["cycle"] == 1

    with pytest.raises(SourceGovernanceError) as exc:
        _activate(service, NEXT_PLATFORM)
    assert exc.value.code == "SOURCE_ALREADY_ACTIVE"

    # A failing active source never falls back to another platform.
    provider = FakeGovernedProvider([], failing_catalog=True)
    failing_database = make_database(tmp_path, "failed.sqlite3")
    failing = MaterialCandidateService(
        failing_database,
        make_settings(tmp_path),
        provider=provider,
        asr=FakeASR(),
        now_fn=lambda: NOW,
    )
    failing.activate_source("default", PLATFORM, "license", "scope")
    with pytest.raises(RuntimeError):
        failing.search(scope_id="default")
    assert failing.active_source("default")["platform"] == PLATFORM


def test_concurrent_activation_keeps_one_cycle(tmp_path: Path) -> None:
    database, _ = _service(tmp_path, FakeGovernedProvider([]))
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        service = MaterialCandidateService(
            database, make_settings(tmp_path), now_fn=lambda: NOW
        )
        try:
            service.activate_source("default", PLATFORM, "license", "scope")
            with lock:
                outcomes.append("activated")
        except SourceGovernanceError as exc:
            with lock:
                outcomes.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["SOURCE_ALREADY_ACTIVE", "activated"]


# ---------------------------------------------------------------------------
# 2. en-US exclusion at every entry
# ---------------------------------------------------------------------------

def test_en_us_is_excluded_from_discovery_import_and_ingestion(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([item("us-1", accent="en-US"), item("uk-1")])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    assert [candidate["provider_item_id"] for candidate in result["candidates"]] == ["uk-1"]
    assert result["rejection_summary"].get("accent_excluded") == 1
    assert [row["provider_item_id"] for row in result["quarantined"]] == ["us-1"]
    assert result["quarantined"][0]["reason_code"] == "ACCENT_EXCLUDED_EN_US"

    # The excluded item never even reached a download.
    assert not any(download.startswith("us-1") for download in provider.downloads)

    # An en-US candidate inserted by hand still cannot be ingested.
    with database.connect() as connection:
        connection.execute(
            "UPDATE material_candidates SET accent = 'en-US' WHERE provider_item_id = 'uk-1'"
        )
    preparation = _preparation(database, tmp_path)
    candidate_id = result["candidates"][0]["candidate_id"]
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(candidate_id, "default", "key-us")
    assert exc.value.code == "ACCENT_NOT_ELIGIBLE"

    # Manual import refuses an explicit en-US declaration.
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path, "api.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            response = client.post(
                "/api/materials",
                json={
                    "material_id": "manual-us",
                    "title": "Manual US",
                    "audio_path": "data/materials/manual-us.wav",
                    "transcript": OFFICIAL_TRANSCRIPT,
                    "accent": "en-US",
                    "timestamped_sentences": [
                        {"text": text, "start_time": index * 5.0, "end_time": (index + 1) * 5.0}
                        for index, text in enumerate(OFFICIAL_TRANSCRIPT.split(". ")[:3])
                    ],
                },
            )
            assert response.status_code == 400
            assert response.json()["detail"]["code"] == "ACCENT_NOT_ELIGIBLE"
    finally:
        main_module.settings = original_settings


# ---------------------------------------------------------------------------
# 2b. Only the canonical British accent is eligible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("accent", ["en-AU", "en-IE", "en_IN", "mixed", "unknown", "klingon", "au"])
def test_non_british_accents_never_become_eligible(accent: str) -> None:
    """Repro 1: only the canonical en-GB may pass the metadata disposition."""
    disposition, _ = assess_disposition(item("acc-centre", accent=accent))
    assert disposition != ELIGIBLE
    assert normalize_accent(accent) != "en-GB"


def test_non_british_accent_is_recorded_and_never_ingested(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([item("au-1", accent="en-AU"), item("uk-1")])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    assert [candidate["provider_item_id"] for candidate in result["candidates"]] == ["uk-1"]

    listing = {row["provider_item_id"]: row for row in service.list_candidates("default")}
    # The declared accent is recorded verbatim, never silently rewritten.
    assert listing["au-1"]["accent"] == "en-AU"
    assert listing["au-1"]["ingestion_status"] == PENDING_REVIEW
    assert listing["au-1"]["failure_code"] == "ACCENT_NOT_UK"

    preparation = _preparation(database, tmp_path)
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(listing["au-1"]["candidate_id"], "default", "key-au")
    assert exc.value.code == "SOURCE_PENDING_REVIEW"


# ---------------------------------------------------------------------------
# 3. Strict platform course order (discovery first, then ordering)
# ---------------------------------------------------------------------------

def test_course_order_cannot_be_bypassed(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([
        item("a1-lesson-2", cefr_level="A1", course="course-a", course_order=1, item_order=2),
        item("a1-lesson-1", cefr_level="A1", course="course-a", course_order=1, item_order=1),
        item("a1-course-b", cefr_level="A1", course="course-b", course_order=2, item_order=1),
        item("a2-lesson-1", cefr_level="A2", course="course-a", course_order=1, item_order=3),
        item("b1-lesson-1", cefr_level="B1", course="course-b", course_order=1, item_order=1),
    ])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    # Provider order is ignored: the platform course order (CEFR, course order,
    # lesson order) decides, and course_order precedes the course name.
    assert [candidate["provider_item_id"] for candidate in result["candidates"]] == [
        "a1-lesson-1",
        "a1-lesson-2",
        "a1-course-b",
    ]
    # Higher levels are discovered and persisted, but the confirmed learning
    # level (A1) keeps them out of the processing/ingestion queue.
    assert {row["provider_item_id"] for row in result["deferred_by_level"]} == {
        "a2-lesson-1",
        "b1-lesson-1",
    }

    qualified = service.next_qualified("default")
    assert qualified["provider_item_id"] == "a1-lesson-1"

    preparation = _preparation(database, tmp_path)
    by_item = {row["provider_item_id"]: row["candidate_id"] for row in service.list_candidates("default")}
    for later in ("a1-lesson-2", "a1-course-b"):
        with pytest.raises(MaterialSelectionError) as exc:
            preparation.prepare(by_item[later], "default", f"key-{later}")
        assert exc.value.code == "COURSE_ORDER_VIOLATION"
    for higher in ("a2-lesson-1", "b1-lesson-1"):
        with pytest.raises(MaterialSelectionError) as exc:
            preparation.prepare(by_item[higher], "default", f"key-{higher}")
        assert exc.value.code == "LEARNING_LEVEL_NOT_CONFIRMED"

    prepared = preparation.prepare(by_item["a1-lesson-1"], "default", "key-a1")
    assert prepared["prepare_status"] == "READY"
    assert prepared["material_id"] == f"{PLATFORM}:a1-lesson-1"
    assert service.next_qualified("default")["provider_item_id"] == "a1-lesson-2"

    with database.connect() as connection:
        material = connection.execute(
            "SELECT ingestion_status, accent, transcript_verified, source_platform FROM materials WHERE material_id = ?",
            (prepared["material_id"],),
        ).fetchone()
        candidate = connection.execute(
            "SELECT ingestion_status FROM material_candidates WHERE candidate_id = ?",
            (by_item["a1-lesson-1"],),
        ).fetchone()
    assert material["ingestion_status"] == "INGESTED"
    assert material["accent"] == "en-GB"
    assert material["transcript_verified"] == 1
    assert material["source_platform"] == PLATFORM
    assert candidate["ingestion_status"] == INGESTED


def test_discovery_persists_the_whole_catalog_before_ordered_processing(tmp_path: Path) -> None:
    """Repro 2: a B1 entry listed first must not be the first download."""
    provider = FakeGovernedProvider([
        item("b1-item", cefr_level="B1", course="course-a", course_order=1, item_order=1),
        item("a1-item", cefr_level="A1", course="course-a", course_order=1, item_order=2),
    ])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default", max_results=1)

    assert [candidate["provider_item_id"] for candidate in result["candidates"]] == ["a1-item"]
    assert provider.downloads, "the qualified entry must be fetched"
    assert all("b1-item" not in download for download in provider.downloads)
    # The complete catalog list is persisted before anything is downloaded, so
    # the deferred B1 entry is visible instead of being lost.
    persisted = {row["provider_item_id"]: row for row in service.list_candidates("default")}
    assert set(persisted) == {"b1-item", "a1-item"}
    assert persisted["b1-item"]["ingestion_status"] == "PENDING"
    assert persisted["b1-item"]["audio_quality"] is None
    assert service.next_qualified("default")["provider_item_id"] == "a1-item"


# ---------------------------------------------------------------------------
# 4. Quarantine with reasons, no transcript leak
# ---------------------------------------------------------------------------

def test_quarantine_reasons_and_blind_listing(tmp_path: Path) -> None:
    provider = FakeGovernedProvider(
        [
            item("unknown-accent", accent=None),
            item("no-license", license_status="UNKNOWN"),
            item("asr-only", transcript_origin="ASR", transcript_verified=False),
            item("no-cefr", cefr_level=None),
            item("noisy", source_url="https://source.example/lessons/noisy.html"),
        ],
        short_audio={"https://source.example/audio/noisy.mp3"},
    )
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")

    assert result["candidates"] == []
    reasons = {row["provider_item_id"]: row["reason_code"] for row in result["pending_review"]}
    assert reasons["unknown-accent"] == "ACCENT_UNVERIFIED"
    assert reasons["no-license"] == "LICENSE_UNVERIFIED"
    assert reasons["asr-only"] == "TRANSCRIPT_UNVERIFIED"
    assert reasons["no-cefr"] == "CEFR_UNVERIFIED"
    # Quality failure is a hard rejection, not a review item.
    assert result["rejection_summary"].get("quality_poor") == 1

    listing = service.list_candidates("default")
    assert listing
    assert all("transcript" not in row for row in listing)
    assert all("timestamped_sentences_json" not in row for row in listing)
    assert {row["ingestion_status"] for row in listing} >= {PENDING_REVIEW, REJECTED}

    # Machine alignment cannot promote a transcript: review is the only path.
    candidate_id = next(row["candidate_id"] for row in listing if row["provider_item_id"] == "asr-only")
    preparation = _preparation(database, tmp_path)
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(candidate_id, "default", "key-asr")
    assert exc.value.code == "SOURCE_PENDING_REVIEW"

    reviewed = service.review_candidate(
        candidate_id,
        "default",
        decision="APPROVE",
        accent="en-GB",
        transcript_verified=True,
        reviewer="TEST",
    )
    assert reviewed["ingestion_status"] == ELIGIBLE
    # Reviewed item is now the next qualified item and can be ingested.
    assert service.next_qualified("default")["candidate_id"] == candidate_id


# ---------------------------------------------------------------------------
# 5. One formal material per source identity; idempotent replay
# ---------------------------------------------------------------------------

def test_duplicate_identity_and_idempotent_replay(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([
        item("lesson-1"),
        item("lesson-1", source_url="https://source.example/alias/lesson-1.html"),
    ])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    assert len(result["candidates"]) == 1
    assert result["rejection_summary"].get("duplicate") == 1
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM material_candidates").fetchone()["n"] == 1

    preparation = _preparation(database, tmp_path)
    candidate_id = result["candidates"][0]["candidate_id"]
    first = preparation.prepare(candidate_id, "default", "key-1")
    replay = preparation.prepare(candidate_id, "default", "key-1")
    assert replay["material_id"] == first["material_id"]
    assert replay["reused"] is True
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(candidate_id, "default", "key-2")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"] == 1

    # Concurrent prepare of the same candidate/key still yields one material.
    concurrent_dir = tmp_path / "concurrent"
    second_provider = FakeGovernedProvider([item("lesson-2")])
    database2 = make_database(concurrent_dir, "concurrent.sqlite3")
    service2 = MaterialCandidateService(
        database2,
        make_settings(concurrent_dir),
        provider=second_provider,
        asr=FakeASR(),
        quality=AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
        now_fn=lambda: NOW,
    )
    service2.activate_source("default", PLATFORM, "license", "scope")
    candidates = service2.search(scope_id="default")["candidates"]
    candidate_two = candidates[0]["candidate_id"]
    outcomes: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        service = MaterialPreparationService(
            database2,
            make_settings(concurrent_dir),
            now_fn=lambda: NOW,
            candidates=MaterialCandidateService(
                database2, make_settings(concurrent_dir), now_fn=lambda: NOW
            ),
        )
        try:
            outcome = service.prepare(candidate_two, "default", "key-shared")
            with lock:
                outcomes.append(outcome["material_id"])
        except Exception as exc:  # pragma: no cover - a failure is the bug
            with lock:
                outcomes.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(outcomes)) == 1, outcomes
    with database2.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# 6. Crash-safe atomic ingestion
# ---------------------------------------------------------------------------

def test_failed_ingestion_leaves_no_half_material(tmp_path: Path, monkeypatch) -> None:
    provider = FakeGovernedProvider([item("lesson-1")])
    database, service = _service(tmp_path, provider)
    _activate(service)
    candidate_id = service.search(scope_id="default")["candidates"][0]["candidate_id"]
    preparation = _preparation(database, tmp_path)

    real_insert = MaterialStore.insert

    def crashing_insert(self, connection, material, **kwargs):
        real_insert(self, connection, material, **kwargs)
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(MaterialStore, "insert", crashing_insert)
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(candidate_id, "default", "key-crash")
    assert exc.value.code == "PREPARE_FAILED"

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) AS n FROM sentences").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) AS n FROM training_progress").fetchone()["n"] == 0
        candidate = connection.execute(
            "SELECT ingestion_status, failure_code FROM material_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    assert candidate["ingestion_status"] == ELIGIBLE
    assert candidate["failure_code"].startswith("PREPARE_FAILED")

    monkeypatch.undo()
    recovered = preparation.prepare(candidate_id, "default", "key-crash")
    assert recovered["prepare_status"] == "READY"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"] == 1
        assert connection.execute("SELECT COUNT(*) AS n FROM sentences").fetchone()["n"] > 0
        assert connection.execute("SELECT COUNT(*) AS n FROM training_progress").fetchone()["n"] == 1


def test_download_failure_is_retryable_not_terminal(tmp_path: Path) -> None:
    provider = FakeGovernedProvider(
        [item("flaky")],
        failing_audio={"https://source.example/audio/flaky.mp3"},
    )
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    assert result["candidates"] == []
    assert result["rejection_summary"].get("download_failed") == 1
    report = service.report("default")
    assert report["dispositions"].get(RETRYABLE_FAILED) == 1
    assert "flaky" in report["retryable_failures"]
    assert report["completion_status"] == "INCOMPLETE"

    # The next run retries instead of treating it as unavailable.
    provider.failing_audio = set()
    retried = service.search(scope_id="default")
    assert [candidate["provider_item_id"] for candidate in retried["candidates"]] == ["flaky"]
    assert service.report("default")["retryable_failures"] == []


# ---------------------------------------------------------------------------
# 7. Honest completion reports
# ---------------------------------------------------------------------------

def test_completion_report_denominators(tmp_path: Path) -> None:
    database, service = _service(
        tmp_path,
        FakeGovernedProvider(
            [item("a1"), item("a2")],
            catalog={
                "catalog_complete": True,
                "known_total": 5,
                "has_cursor": False,
                "catalog_scope": "page-1-of-1",
            },
        ),
    )
    _activate(service)
    service.search(scope_id="default")
    report = service.report("default")
    assert report["discovered"] == 2
    assert report["known_total"] == 5
    assert report["discovered_ratio"] == pytest.approx(0.4)
    assert report["legal_acquisition_ratio"] == pytest.approx(1.0)
    assert report["ingested_ratio"] == pytest.approx(0.0)
    # Discovering is not acquiring: 2 of 5 items are known but unprocessed.
    assert report["remaining_items"] == 2
    assert report["completion_status"] == "INCOMPLETE"

    # A cursor or an unknown total never reports completion.
    cursor_service = MaterialCandidateService(
        make_database(tmp_path, "cursor.sqlite3"),
        make_settings(tmp_path),
        provider=FakeGovernedProvider([item("a1")], catalog={
            "catalog_complete": False, "known_total": None, "has_cursor": True,
        }),
        asr=FakeASR(),
        now_fn=lambda: NOW,
    )
    cursor_service.activate_source("default", PLATFORM, "license", "scope")
    cursor_service.search(scope_id="default")
    cursor_report = cursor_service.report("default")
    assert cursor_report["completion_status"] == "INCOMPLETE"
    assert cursor_report["discovered_ratio"] is None

    unknown_total = MaterialCandidateService(
        make_database(tmp_path, "unknown.sqlite3"),
        make_settings(tmp_path),
        provider=FakeGovernedProvider([item("a1")], catalog={
            "catalog_complete": True, "known_total": None, "has_cursor": False,
        }),
        asr=FakeASR(),
        now_fn=lambda: NOW,
    )
    unknown_total.activate_source("default", PLATFORM, "license", "scope")
    unknown_total.search(scope_id="default")
    assert unknown_total.report("default")["completion_status"] == "UNKNOWN_TOTAL"


# ---------------------------------------------------------------------------
# 8. Confirmed-only switching
# ---------------------------------------------------------------------------

def test_switch_requires_current_report_and_explicit_confirmation(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([item("a1"), item("a2", accent=None)])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    candidate_id = result["candidates"][0]["candidate_id"]
    preparation = _preparation(database, tmp_path)

    # Unqualified work blocks the switch.
    assert service.report("default")["completion_status"] == "INCOMPLETE"
    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", service.report("default")["report_id"], NEXT_PLATFORM, "license", "scope"
        )
    assert exc.value.code in ("SOURCE_ITEMS_PENDING", "CONFIRMATION_STALE")

    preparation.prepare(candidate_id, "default", "key-a1")
    report = service.report("default")
    assert report["completion_status"] == "COMPLETE"
    assert len(report["pending_manual_items"]) == 1

    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], NEXT_PLATFORM, "", "scope", acknowledge_pending=True
        )
    assert exc.value.code == "SOURCE_NOT_APPROVED"

    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], "BBC_LEARNING_ENGLISH", "license", "scope",
            acknowledge_pending=True,
        )
    assert exc.value.code == "SOURCE_ORDER_VIOLATION"

    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], NEXT_PLATFORM, "license", "scope"
        )
    assert exc.value.code == "PENDING_ITEMS_REQUIRE_DECISION"

    # Confirmation is bound to one report version.
    regenerated = service.report("default")
    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], NEXT_PLATFORM, "license", "scope",
            acknowledge_pending=True,
        )
    assert exc.value.code == "CONFIRMATION_STALE"

    switched = service.confirm_switch(
        "default", regenerated["report_id"], NEXT_PLATFORM, "license", "scope",
        acknowledge_pending=True, confirmed_by="TEST",
    )
    assert switched["platform"] == NEXT_PLATFORM
    assert switched["cycle"] == 2

    # Old-platform candidates cannot be ingested after the switch.
    stale_candidate = next(
        row["candidate_id"]
        for row in service.list_candidates("default")
        if row["ingestion_status"] == PENDING_REVIEW
    )
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(stale_candidate, "default", "key-stale")
    assert exc.value.code == "SOURCE_NOT_APPROVED"

    # ... but the original idempotent replay still returns the first result.
    replay = preparation.prepare(candidate_id, "default", "key-a1")
    assert replay["reused"] is True


def test_switch_is_rejected_while_retryable_failures_remain(tmp_path: Path) -> None:
    provider = FakeGovernedProvider(
        [item("a1")],
        failing_audio={"https://source.example/audio/a1.mp3"},
        catalog={"catalog_complete": True, "known_total": 1, "has_cursor": False},
    )
    _, service = _service(tmp_path, provider)
    _activate(service)
    service.search(scope_id="default")
    report = service.report("default")
    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], NEXT_PLATFORM, "license", "scope",
            acknowledge_pending=True,
        )
    assert exc.value.code == "SOURCE_ITEMS_PENDING"


# ---------------------------------------------------------------------------
# 8b. Report version, catalog completeness, source lock, authorization
# ---------------------------------------------------------------------------

class WeekClock:
    """Advancing clock for the eight-week confirmation records."""

    def __init__(self) -> None:
        self.now = NOW

    def advance_weeks(self, weeks: int) -> None:
        self.now = self.now + timedelta(days=7 * weeks)


def test_report_is_invalidated_by_later_discovery(tmp_path: Path) -> None:
    """Repro 3: a report generated on an empty catalog cannot be reused."""
    provider = FakeGovernedProvider([])
    _, service = _service(tmp_path, provider)
    _activate(service)
    service.search(scope_id="default")
    report = service.report("default")
    assert report["completion_status"] == "COMPLETE"

    # A qualified entry appears after the report was generated.
    provider.items = [item("late-1")]
    provider.catalog = {
        "catalog_complete": True,
        "known_total": 1,
        "has_cursor": False,
        "catalog_scope": "fixture-snapshot",
    }
    service.search(scope_id="default")

    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], NEXT_PLATFORM, "license", "scope",
            acknowledge_pending=True,
        )
    assert exc.value.code == "CONFIRMATION_STALE"

    fresh = service.report("default")
    assert fresh["report_id"] != report["report_id"]
    assert fresh["discovered"] == 1


def test_switch_is_blocked_while_catalog_entries_are_undiscovered(tmp_path: Path) -> None:
    """Repro 4: a declared total of five with three discovered is not complete."""
    provider = FakeGovernedProvider(
        [item("a1", item_order=1), item("a2", item_order=2), item("a3", item_order=3)],
        catalog={
            "catalog_complete": True,
            "known_total": 5,
            "has_cursor": False,
            "catalog_scope": "page-1-of-1",
        },
    )
    database, service = _service(tmp_path, provider)
    _activate(service)
    service.search(scope_id="default")
    report = service.report("default")
    assert report["discovered"] == 3
    assert report["known_total"] == 5
    assert report["completion_status"] == "INCOMPLETE"

    preparation = _preparation(database, tmp_path)
    for key in ("key-a1", "key-a2", "key-a3"):
        preparation.prepare_next("default", key)

    report = service.report("default")
    assert report["dispositions"].get(INGESTED) == 3
    assert report["completion_status"] == "INCOMPLETE", "3 of 5 discovered is not completion"
    with pytest.raises(SourceGovernanceError) as exc:
        service.confirm_switch(
            "default", report["report_id"], NEXT_PLATFORM, "license", "scope",
            acknowledge_pending=True,
        )
    assert exc.value.code in ("SOURCE_CATALOG_INCOMPLETE", "CONFIRMATION_STALE")


def test_single_active_source_cannot_be_bypassed_by_scope(tmp_path: Path) -> None:
    """Repro 6: one installation has exactly one active source."""
    provider = FakeGovernedProvider([item("a1")])
    _, service = _service(tmp_path, provider)
    _activate(service)

    with pytest.raises(SourceGovernanceError) as exc:
        service.activate_source("other-scope", NEXT_PLATFORM, "license", "scope")
    assert exc.value.code == "SOURCE_ALREADY_ACTIVE"
    with pytest.raises(SourceGovernanceError) as exc:
        service.activate_source("other-scope", PLATFORM, "license", "scope")
    assert exc.value.code == "SOURCE_ALREADY_ACTIVE"

    state = service.source_state("other-scope")
    assert state["active_source"]["platform"] == PLATFORM
    assert state["active_source"]["cycle"] == 1


def test_unverified_license_is_recorded_without_download(tmp_path: Path) -> None:
    """Repro 7: authorization is confirmed before any audio is fetched."""
    provider = FakeGovernedProvider([
        item("unknown-license", license_status="UNKNOWN"),
        item("uk-1", item_order=2),
    ])
    _, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")

    assert [candidate["provider_item_id"] for candidate in result["candidates"]] == ["uk-1"]
    assert not any("unknown-license" in download for download in provider.downloads)
    row = {
        candidate["provider_item_id"]: candidate
        for candidate in service.list_candidates("default")
    }["unknown-license"]
    assert row["ingestion_status"] == PENDING_REVIEW
    assert row["failure_code"] == "LICENSE_UNVERIFIED"
    assert row["audio_quality"] is None

    report = service.report("default")
    assert "unknown-license" in report["pending_manual_items"]
    with pytest.raises(SourceGovernanceError) as exc:
        service.review_candidate(row["candidate_id"], "default", decision="APPROVE", accent="en-GB")
    assert exc.value.code == "LICENSE_UNVERIFIED"


class _ContradictingASR:
    """Independent ASR output that shares almost nothing with the official script."""

    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        return [
            RecognizedSegment(
                text=f"Completely unrelated audio words number {index} here now.",
                start_time=index * 1.9,
                end_time=index * 1.9 + 1.8,
            )
            for index in range(32)
        ]


def test_transcript_contradicting_asr_stays_pending(tmp_path: Path) -> None:
    """A declared transcript is not verified when the ASR describes other content."""
    provider = FakeGovernedProvider([item("contradicted")])
    database = make_database(tmp_path)
    service = MaterialCandidateService(
        database,
        make_settings(tmp_path),
        provider=provider,
        asr=_ContradictingASR(),
        quality=AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
        now_fn=lambda: NOW,
    )
    _activate(service)
    result = service.search(scope_id="default")
    assert result["candidates"] == []

    row = service.list_candidates("default")[0]
    assert row["ingestion_status"] == PENDING_REVIEW
    assert row["failure_code"] == "TRANSCRIPT_CONTRADICTED"
    assert row["transcript_verified"] == 1, "the declaration alone is not evidence"

    with pytest.raises(MaterialSelectionError) as exc:
        _preparation(database, tmp_path).prepare(row["candidate_id"], "default", "key-c")
    assert exc.value.code == "SOURCE_PENDING_REVIEW"


def test_learning_level_requires_the_eight_week_confirmation(tmp_path: Path) -> None:
    """Repro 5: a client stage cannot unlock a higher CEFR level."""
    provider = FakeGovernedProvider([
        item("a1", cefr_level="A1", item_order=1),
        item("a2", cefr_level="A2", item_order=2),
    ])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default", speed_stage="STAGE_3")
    assert [candidate["provider_item_id"] for candidate in result["candidates"]] == ["a1"]
    assert service.learning_level("default") == "A1"

    by_item = {
        candidate["provider_item_id"]: candidate["candidate_id"]
        for candidate in service.list_candidates("default")
    }
    preparation = _preparation(database, tmp_path)
    for stage in ("STAGE_1", "STAGE_2", "STAGE_3"):
        with pytest.raises(MaterialSelectionError) as exc:
            preparation.prepare(by_item["a2"], "default", f"key-{stage}")
        assert exc.value.code == "LEARNING_LEVEL_NOT_CONFIRMED"

    # Only the existing eight-week confirmation records may raise the ceiling.
    clock = WeekClock()
    weekly = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: clock.now)
    difficulty = DifficultyProgressionService(database, weekly, now_fn=lambda: clock.now)
    for index in range(1, 9):
        weekly.create(
            week_id=f"W{index}",
            period_start="2026-01-01",
            period_end="2026-01-07",
            dictation_required=True,
            reading_required=False,
        )
        weekly.record_dictation(f"W{index}", score=90.0, passed=True)
        difficulty.evaluate_weekly_gate("default", f"W{index}")
        clock.advance_weeks(1)
    prompt = difficulty.ensure_prompt("default")
    difficulty.decide_upgrade("default", prompt["prompt_id"], "UPGRADE_CONFIRMED", "upgrade-key")
    assert service.learning_level("default") == "A2"

    refreshed = service.search(scope_id="default")
    assert {candidate["provider_item_id"] for candidate in refreshed["candidates"]} == {"a1", "a2"}
    assert preparation.prepare_next("default", "key-a1")["material_id"] == f"{PLATFORM}:a1"
    assert preparation.prepare_next("default", "key-a2")["material_id"] == f"{PLATFORM}:a2"


def test_prepare_rechecks_the_latest_candidate_state(tmp_path: Path) -> None:
    """Prepare validates against the latest rows inside the write transaction."""
    provider = FakeGovernedProvider([item("a1-1", item_order=1), item("a1-2", item_order=2)])
    database, service = _service(tmp_path, provider)
    _activate(service)
    result = service.search(scope_id="default")
    by_item = {candidate["provider_item_id"]: candidate["candidate_id"] for candidate in result["candidates"]}

    # A later discovery inserts an entry that now comes first in course order.
    provider.items = [item("a1-0", item_order=0), *provider.items]
    provider.catalog = {
        "catalog_complete": True,
        "known_total": 3,
        "has_cursor": False,
        "catalog_scope": "fixture-snapshot",
    }
    service.search(scope_id="default")

    preparation = _preparation(database, tmp_path)
    with pytest.raises(MaterialSelectionError) as exc:
        preparation.prepare(by_item["a1-1"], "default", "key-late")
    assert exc.value.code == "COURSE_ORDER_VIOLATION"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) AS n FROM sentences").fetchone()["n"] == 0


def test_concurrent_confirmation_keeps_one_active_source(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([item("a1")])
    database, service = _service(tmp_path, provider)
    _activate(service)
    service.search(scope_id="default")
    preparation = _preparation(database, tmp_path)
    preparation.prepare_next("default", "key-a1")
    report = service.report("default")
    assert report["completion_status"] == "COMPLETE"

    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        local = MaterialCandidateService(database, make_settings(tmp_path), now_fn=lambda: NOW)
        try:
            local.confirm_switch(
                "default", report["report_id"], NEXT_PLATFORM, "license", "scope",
                acknowledge_pending=True,
            )
            with lock:
                outcomes.append("switched")
        except SourceGovernanceError as exc:
            with lock:
                outcomes.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["SOURCE_ORDER_VIOLATION", "switched"]
    active = service.active_source("default")
    assert active["platform"] == NEXT_PLATFORM
    assert active["cycle"] == 2


# ---------------------------------------------------------------------------
# 9. Legacy entry point and API surface stay governed
# ---------------------------------------------------------------------------

def test_legacy_next_entry_refuses_without_active_source(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    service = MaterialSearchService(
        database,
        make_settings(tmp_path),
        providers=[FakeGovernedProvider([item("a1")])],
        asr=FakeASR(),
    )
    with pytest.raises(ValueError, match="SOURCE_NOT_APPROVED"):
        service.search_next()

    service.candidates.activate_source("default", PLATFORM, "license", "scope")
    fetched = service.search_next()
    assert fetched["material_id"] == f"{PLATFORM}:a1"
    assert fetched["source_platform"] == PLATFORM
    assert fetched["ingestion_status"] == "INGESTED"

    # Nothing else is qualified, so a second fetch does not invent a source.
    with pytest.raises(ValueError, match="No qualified material"):
        service.search_next()


def test_api_surface_uses_the_governed_flow(tmp_path: Path) -> None:
    provider = FakeGovernedProvider([item("a1")])
    database, service = _service(tmp_path, provider)
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            client.app.state.material_candidates = service
            client.app.state.material_preparation = _preparation(database, tmp_path)
            assert client.get("/api/p1/source/state").json()["active_source"] is None

            searched = client.post("/api/p1/material-candidates/search", json={"scope_id": "default"})
            assert searched.status_code == 409
            assert searched.json()["detail"]["code"] == "SOURCE_NOT_APPROVED"

            activated = client.post(
                "/api/p1/source/activate",
                json={
                    "scope_id": "default",
                    "platform": PLATFORM,
                    "license_reference": "fixture-license",
                    "catalog_scope": "fixture-snapshot",
                },
            )
            assert activated.status_code == 200, activated.text
            assert activated.json()["cycle"] == 1

            discovered = client.post("/api/p1/material-candidates/search", json={"scope_id": "default"})
            assert discovered.status_code == 200, discovered.text
            payload = discovered.json()
            assert payload["candidates"][0]["provider_item_id"] == "a1"

            listing = client.get("/api/p1/material-candidates").json()
            assert listing and all("transcript" not in row for row in listing)

            candidate_id = payload["candidates"][0]["candidate_id"]
            prepared = client.post(
                f"/api/p1/material-candidates/{candidate_id}/prepare",
                json={"scope_id": "default", "idempotency_key": "api-key-1"},
            )
            assert prepared.status_code == 200, prepared.text
            report = client.post("/api/p1/source/report", json={"scope_id": "default"})
            assert report.status_code == 200
            assert report.json()["completion_status"] == "COMPLETE"
    finally:
        main_module.settings = original_settings


# ---------------------------------------------------------------------------
# 10. Historical / unverified material stays isolated, never deleted
# ---------------------------------------------------------------------------

def test_quarantined_material_is_isolated_from_the_new_weekly_pool(tmp_path: Path) -> None:
    """A material created without governance evidence stays trainable history
    but never backfills the new weekly question pool."""
    from app.core.weekly import WeeklyAssessmentService
    from tests.fixtures import DEFAULT_SENTENCES, create_material

    database = make_database(tmp_path)
    create_material(database, "legacy-1")
    with database.connect() as connection:
        material = connection.execute(
            "SELECT ingestion_status, accent, transcript_verified FROM materials WHERE material_id = 'legacy-1'"
        ).fetchone()
        connection.execute(
            "UPDATE training_progress SET current_state = 'DICTATION_PART_2' WHERE material_id = 'legacy-1'"
        )
    assert material["ingestion_status"] == "QUARANTINED"
    assert material["accent"] is None
    assert material["transcript_verified"] == 0

    weekly = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: NOW)
    weekly.create(
        week_id="W1",
        period_start="2026-11-16",
        period_end="2026-11-22",
        dictation_required=True,
        reading_required=False,
    )
    with pytest.raises(ValueError, match="verified material"):
        weekly.create_test_items("W1", count=2)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM weekly_test_items").fetchone()["n"] == 0
        # History is preserved: the material, its sentences and its progress stay.
        assert connection.execute(
            "SELECT current_state FROM training_progress WHERE material_id = 'legacy-1'"
        ).fetchone()["current_state"] == "DICTATION_PART_2"
        assert connection.execute(
            "SELECT COUNT(*) AS n FROM sentences WHERE material_id = 'legacy-1'"
        ).fetchone()["n"] == len(DEFAULT_SENTENCES)

    # A verified material from a non-active source is still excluded.
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE materials SET ingestion_status = 'INGESTED', transcript_verified = 1,
                   accent = 'en-GB', source_platform = 'OTHER_PLATFORM' 
             WHERE material_id = 'legacy-1'
            """
        )
    weekly_second = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: NOW)
    weekly_second.create(
        week_id="W2",
        period_start="2026-11-16",
        period_end="2026-11-22",
        dictation_required=True,
        reading_required=False,
    )
    _activate(MaterialCandidateService(database, make_settings(tmp_path), now_fn=lambda: NOW))
    with pytest.raises(ValueError, match="verified material"):
        weekly_second.create_test_items("W2", count=2)

    # A non-British accent never backfills the pool, even from the active source.
    with database.connect() as connection:
        connection.execute(
            "UPDATE materials SET accent = 'en-AU', license_status = 'CLEARED' WHERE material_id = 'legacy-1'"
        )
    weekly_third = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: NOW)
    weekly_third.create(
        week_id="W3",
        period_start="2026-11-16",
        period_end="2026-11-22",
        dictation_required=True,
        reading_required=False,
    )
    with pytest.raises(ValueError, match="verified material"):
        weekly_third.create_test_items("W3", count=2)

    # ... and an uncleared license keeps it out even with the right accent.
    with database.connect() as connection:
        connection.execute(
            "UPDATE materials SET accent = 'en-GB', license_status = 'UNKNOWN' WHERE material_id = 'legacy-1'"
        )
    weekly_fourth = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: NOW)
    weekly_fourth.create(
        week_id="W4",
        period_start="2026-11-16",
        period_end="2026-11-22",
        dictation_required=True,
        reading_required=False,
    )
    with pytest.raises(ValueError, match="verified material"):
        weekly_fourth.create_test_items("W4", count=2)

    # Only verified material of the active source with the canonical British
    # accent and a cleared license fills the pool.
    with database.connect() as connection:
        connection.execute(
            "UPDATE materials SET license_status = 'CLEARED' WHERE material_id = 'legacy-1'"
        )
    weekly_fifth = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: NOW)
    weekly_fifth.create(
        week_id="W5",
        period_start="2026-11-16",
        period_end="2026-11-22",
        dictation_required=True,
        reading_required=False,
    )
    items = weekly_fifth.create_test_items("W5", count=2)
    assert len(items) == 2
    assert all(item["text"] in DEFAULT_SENTENCES for item in items)


def test_quarantined_import_is_not_offered_for_new_training(tmp_path: Path) -> None:
    """A manual import stays a viewable record but never starts new training."""
    texts = OFFICIAL_TRANSCRIPT.split(". ")[:3]
    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path, "homepage.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            created = client.post(
                "/api/materials",
                json={
                    "material_id": "manual-1",
                    "title": "Manual import",
                    "audio_path": "data/materials/manual-1.wav",
                    "transcript": OFFICIAL_TRANSCRIPT,
                    "timestamped_sentences": [
                        {"text": text, "start_time": index * 5.0, "end_time": (index + 1) * 5.0}
                        for index, text in enumerate(texts)
                    ],
                },
            )
            assert created.status_code == 201, created.text
            assert created.json()["ingestion_status"] == "QUARANTINED"

            # The record is preserved ...
            assert [row["material_id"] for row in client.get("/api/materials").json()] == ["manual-1"]
            # ... but it is not offered for a new training session.
            assert client.get("/api/materials/available").json() == []

            # Training that already started stays resumable.
            with client.app.state.database.connect() as connection:
                connection.execute(
                    "UPDATE training_progress SET current_state = 'DICTATION_PART_2' WHERE material_id = 'manual-1'"
                )
            available = client.get("/api/materials/available").json()
            assert [row["material_id"] for row in available] == ["manual-1"]
            assert available[0]["current_state"] == "DICTATION_PART_2"
    finally:
        main_module.settings = original_settings

