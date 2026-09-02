"""Regression locks for the five reported P1 critical defects:

F1 provider/quality failures recover via bounded retry
F2 missing weeks and cross-stage records must not pollute the streak
F3 FAILED materials must be gated out of the P0 training core
F4 lost-response gate retries must leave profile consistent; racing
   duplicates must not 500
F5 candidate ranking dimensions and search audit persistence
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.adapters.speech import RecognizedSegment
from app.core.audio_quality import AudioQualityAnalyzer, QualityThresholds
from app.core.difficulty_progression import DifficultyProgressionService
from app.core.material_candidates import MaterialCandidateService
from app.core.material_preparation import MaterialPreparationService
from app.core.weekly import WeeklyAssessmentService
from app.db.connection import Database
from tests.fixtures import DEFAULT_SENTENCES, make_database, make_settings, make_sine_wav

NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)
DUR_MIN, DUR_MAX = 1.0, 2.0


# ------------------------- F1: bounded retry -------------------------

class FlakyDownloadProvider:
    def __init__(self, tmp_path: Path, fail_times: int = 1) -> None:
        self.tmp_path = tmp_path
        self.fail_times = fail_times
        self.attempts = 0

    def _list_episodes(self, zone_ids=None) -> list[tuple[str, str, str, float | None]]:
        return [("https://voa.example/a/1.html", "https://voa.example/a1.mp3", "Flaky", 80.0)]

    def download_audio(self, mp3_url: str, audio_id: str, work_dir: Path) -> Path:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("transient download failure")
        wav = work_dir / f"{audio_id}.wav"
        make_sine_wav(wav, segments=[(65.0, 12000.0), (1.0, 0.0)])
        return wav


class SimpleASR:
    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        return [
            RecognizedSegment(text=f"Sample sentence number {index} with enough words here.", start_time=index * 2.0, end_time=index * 2.0 + 1.8)
            for index in range(30)
        ]


def test_f1_transient_provider_failure_recovers_by_retry(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    provider = FlakyDownloadProvider(tmp_path, fail_times=1)
    service = MaterialCandidateService(
        database, make_settings(tmp_path), provider=provider, asr=SimpleASR(),
        quality=AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
        now_fn=lambda: NOW,
    )
    monkeypatch.setattr("app.core.material_candidates.RETRY_BACKOFF_SECONDS", 0.0)
    result = service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    assert provider.attempts == 2
    assert len(result["candidates"]) == 1
    assert "download_failed" not in result["rejection_summary"]


def test_f1_exhausted_retry_rejects_entry(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    provider = FlakyDownloadProvider(tmp_path, fail_times=99)
    service = MaterialCandidateService(
        database, make_settings(tmp_path), provider=provider, asr=SimpleASR(),
        quality=AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
        now_fn=lambda: NOW,
    )
    monkeypatch.setattr("app.core.material_candidates.RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr("app.core.material_candidates.RETRY_LIMIT", 2)
    result = service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    assert result["candidates"] == []
    assert result["rejection_summary"].get("download_failed", 0) == 1


# ------------------------- F2: streak invariants -------------------------

class WeekClock:
    def __init__(self) -> None:
        self.now = NOW

    def advance_weeks(self, weeks: int) -> None:
        self.now = self.now + timedelta(days=7 * weeks)


def _difficulty_setup(tmp_path: Path) -> tuple[Database, WeekClock, DifficultyProgressionService, WeeklyAssessmentService]:
    database = make_database(tmp_path)
    clock = WeekClock()
    weekly = WeeklyAssessmentService(database, make_settings(tmp_path), now_fn=lambda: clock.now)
    difficulty = DifficultyProgressionService(database, weekly, now_fn=lambda: clock.now)
    return database, clock, difficulty, weekly


def _pass_week(weekly: WeeklyAssessmentService, week_id: str, score: float = 90.0) -> None:
    weekly.create(week_id=week_id, period_start="2026-01-01", period_end="2026-01-07", dictation_required=True, reading_required=False)
    weekly.record_dictation(week_id, score=score, passed=True)


def test_f2_cross_stage_records_do_not_pollute_streak(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _difficulty_setup(tmp_path)
    for index in range(1, 9):
        _pass_week(weekly, f"S1-W{index}")
        difficulty.evaluate_weekly_gate("default", f"S1-W{index}")
        clock.advance_weeks(1)
    prompt = difficulty.ensure_prompt("default")
    difficulty.decide_upgrade("default", prompt["prompt_id"], "UPGRADE_CONFIRMED", "k1")
    assert difficulty.get_profile("default")["current_stage"] == "STAGE_2"
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 0

    # First STAGE_2 week passes: the streak must restart at 1, never inherit
    # the STAGE_1 records (same calendar weeks are adjacent).
    _pass_week(weekly, "S2-W1")
    difficulty.evaluate_weekly_gate("default", "S2-W1")
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1


def test_f2_missing_week_resets_streak(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _difficulty_setup(tmp_path)
    for index in range(1, 5):
        _pass_week(weekly, f"W{index}")
        difficulty.evaluate_weekly_gate("default", f"W{index}")
        clock.advance_weeks(1)
    # Skip two calendar weeks entirely (W5, W6 missing).
    clock.advance_weeks(2)
    _pass_week(weekly, "W7")
    difficulty.evaluate_weekly_gate("default", "W7")
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1


# ------------------------- F3: FAILED material gating -------------------------

def _ready_material(db: Database, tmp_path: Path, material_id: str = "gate-m1") -> str:
    audio = tmp_path / "data" / "materials" / f"{material_id}.wav"
    make_sine_wav(audio, segments=[(2.0, 12000.0), (0.5, 0.0)])
    from tests.fixtures import create_material

    create_material(db, material_id)
    with db.connect() as connection:
        connection.execute("UPDATE materials SET audio_path = ? WHERE material_id = ?", (str(audio), material_id))
    return material_id


def test_f3_failed_material_is_gated_from_training(tmp_path: Path) -> None:
    from app.core.dictation_service import DictationService
    from app.core.states import TransitionError
    from app.core.training_events import TrainingEventService

    database = make_database(tmp_path)
    material_id = _ready_material(database, tmp_path)
    with database.connect() as connection:
        connection.execute("UPDATE materials SET prepare_status = 'FAILED' WHERE material_id = ?", (material_id,))

    events = TrainingEventService(database)
    with pytest.raises(TransitionError, match="not ready"):
        events.complete_first_listen(material_id)
    with pytest.raises(ValueError, match="not ready"):
        DictationService(database).submit(
            material_id=material_id, sentence_id=f"{material_id}-sentence-001",
            user_text="x", listen_count=1, operation_id="op-gate",
        )

    original_settings = main_module.settings
    main_module.settings = make_settings(tmp_path)
    try:
        with TestClient(main_module.app) as client:
            audio = client.get(f"/api/materials/{material_id}/audio")
            assert audio.status_code == 409
            first_listen = client.post(f"/api/materials/{material_id}/first-listen/complete")
            assert first_listen.status_code == 409
    finally:
        main_module.settings = original_settings

    # Restoring READY re-enables training.
    with database.connect() as connection:
        connection.execute("UPDATE materials SET prepare_status = 'READY' WHERE material_id = ?", (material_id,))
    assert events.complete_first_listen(material_id).current_state.value == "FIRST_COMPREHENSION_CHECK"


# ------------------------- F4: gate retry & race consistency -------------------------

def test_f4_lost_response_retry_recomputes_profile(tmp_path: Path) -> None:
    database, clock, difficulty, weekly = _difficulty_setup(tmp_path)
    _pass_week(weekly, "W1")
    difficulty.evaluate_weekly_gate("default", "W1")
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1

    # Simulate an inconsistent profile (e.g. first response lost mid-write).
    with database.connect() as connection:
        connection.execute(
            "UPDATE training_difficulty_profiles SET consecutive_pass_weeks = 0 WHERE scope_id = 'default'"
        )
    # Retry of the same week must fix the profile, not just return reused.
    result = difficulty.evaluate_weekly_gate("default", "W1")
    assert result["reused"] is True
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1


def test_f4_concurrent_duplicate_evaluation_never_500s(tmp_path: Path) -> None:
    import threading

    database, clock, difficulty, weekly = _difficulty_setup(tmp_path)
    _pass_week(weekly, "W1")
    results: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            outcome = difficulty.evaluate_weekly_gate("default", "W1")
            with lock:
                results.append(("ok", outcome["record"]["gate_id"]))
        except Exception as exc:  # pragma: no cover - failure is the bug
            with lock:
                results.append(("error", repr(exc)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(kind == "ok" for kind, _ in results), results
    assert len({gate_id for _, gate_id in results}) == 1, "one record, resolved by both"
    assert difficulty.get_profile("default")["consecutive_pass_weeks"] == 1


# ------------------------- F5: ranking & audit -------------------------

def test_f5_ranking_uses_all_four_dimensions() -> None:
    database = make_database(Path(__file__).parent / "tmp-f5")
    service = MaterialCandidateService(database, make_settings(Path(__file__).parent))
    candidates = [
        {"candidate_id": "a", "audio_quality": "Clear", "transcript_status": "COMPLETE", "duration_seconds": 110.0, "speech_rate_wpm": 140.0},
        {"candidate_id": "b", "audio_quality": "Clear", "transcript_status": "COMPLETE", "duration_seconds": 90.0, "speech_rate_wpm": 110.0},
        {"candidate_id": "c", "audio_quality": "Acceptable", "transcript_status": "COMPLETE", "duration_seconds": 100.0, "speech_rate_wpm": 120.0},
        {"candidate_id": "d", "audio_quality": "Clear", "transcript_status": "INCOMPLETE", "duration_seconds": 100.0, "speech_rate_wpm": 120.0},
    ]
    ranked = service._rank(candidates, 1.0, 2.0, "STAGE_1")
    # Clear+COMPLETE before Clear+INCOMPLETE; Acceptable only tops up when
    # Clear < 3 (here three Clears fill the quota, so 'c' is excluded).
    assert [c["candidate_id"] for c in ranked] == ["b", "a", "d"]


def test_f5_search_audit_is_persisted(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    provider = FlakyDownloadProvider(tmp_path, fail_times=0)
    service = MaterialCandidateService(
        database, make_settings(tmp_path), provider=provider, asr=SimpleASR(),
        quality=AudioQualityAnalyzer(QualityThresholds(min_duration_seconds=30.0)),
        now_fn=lambda: NOW,
    )
    service.search(scope_id="default", speed_stage="STAGE_1", target_duration_min=DUR_MIN, target_duration_max=DUR_MAX)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT batch_id, speed_stage, provider, analyzer_version, threshold_config_version, candidate_count, rejection_summary_json FROM search_audits"
        ).fetchone()
    assert row is not None
    assert row["speed_stage"] == "STAGE_1"
    assert row["provider"] == "VOA"
    assert row["analyzer_version"] == "1.0"
    assert row["threshold_config_version"] == "1.0"
    assert row["candidate_count"] == 1
    assert row["rejection_summary_json"] != ""
