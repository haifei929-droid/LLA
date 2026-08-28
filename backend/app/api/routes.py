from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.api.schemas import (
    ComprehensionCheckRequest,
    DictationSubmitRequest,
    MaterialCreateRequest,
    P1CandidateSearchRequest,
    P1PrepareRequest,
    P1UpgradeDecisionRequest,
    P1WeeklyGateRequest,
    P2MemoryConfigRequest,
    P2SuggestionActionRequest,
    ReadingAssessmentRequest,
    RecordingScoreRequest,
    TimeLogStartRequest,
    TimeLogStopRequest,
    WeeklyAssessmentCreateRequest,
    WeeklyDictationRequest,
    WeeklyReadingRequest,
    WeeklyTestItemDictationRequest,
    WeeklyTestItemsRequest,
)
from app.core.material_preparation import MaterialSelectionError
from app.core.difficulty_progression import DifficultyError
from app.core.difficulty_history import DifficultyHistoryError
from app.core.memory_deepening import MemoryConfigError
from app.core.materials import MaterialExistsError, MaterialStore
from app.core.states import TransitionError
from app.core.training_events import progress_payload
from app.preprocess.material import MaterialPreprocessError, MaterialPreprocessor, TimestampedSentence

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@router.post("/materials", status_code=status.HTTP_201_CREATED)
def create_material(payload: MaterialCreateRequest, request: Request) -> dict[str, object]:
    try:
        material = MaterialPreprocessor().process(
            material_id=payload.material_id,
            title=payload.title,
            audio_path=payload.audio_path,
            transcript=payload.transcript,
            timestamped_sentences=[TimestampedSentence(**item.model_dump()) for item in payload.timestamped_sentences],
            natural_part_boundaries=payload.natural_part_boundaries,
        )
        request.app.state.material_store.create(material)
    except MaterialExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (MaterialPreprocessError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Material ID already exists") from exc
    return {
        "material_id": material.material_id,
        "title": material.title,
        "duration_seconds": material.duration_seconds,
        "sentence_count": len(material.sentences),
        "status": material.status,
    }


@router.get("/materials")
def list_materials(request: Request) -> list[dict[str, object]]:
    return request.app.state.material_store.list()


@router.get("/materials/search")
def search_materials(request: Request, q: str = "") -> list[dict[str, object]]:
    return request.app.state.material_store.search(q)


@router.get("/materials/{material_id}")
def get_material(material_id: str, request: Request) -> dict[str, object]:
    material = request.app.state.material_store.get(material_id)
    if material is None:
        raise HTTPException(status_code=404, detail=f"No material exists for {material_id}")
    return material


@router.get("/materials/{material_id}/audio")
def get_material_audio(material_id: str, request: Request) -> FileResponse:
    material = request.app.state.material_store.get(material_id)
    if material is None:
        raise HTTPException(status_code=404, detail=f"No material exists for {material_id}")
    if material.get("prepare_status", "READY") != "READY":
        raise HTTPException(status_code=409, detail="Material is not ready for training")
    audio_path = Path(str(material["audio_path"]))
    if not audio_path.is_absolute():
        audio_path = request.app.state.settings.project_root / audio_path
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is not available on the local filesystem")
    return FileResponse(audio_path)


@router.post("/materials/next")
def search_next_material(request: Request) -> dict[str, object]:
    """Auto-search the next material by difficulty rules and learning pace."""
    try:
        return request.app.state.material_search.search_next()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/materials/{material_id}/skip")
def skip_material(material_id: str, request: Request) -> dict[str, object]:
    """User skips the current material and asks for a different one."""
    try:
        return request.app.state.material_search.skip(material_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ================= P1: material candidates & difficulty progression =================

def _p1_error(exc: MaterialSelectionError | DifficultyError) -> HTTPException:
    status = 409 if exc.code in ("IDEMPOTENCY_CONFLICT", "PROMPT_ALREADY_RESOLVED") else 400
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.post("/p1/material-candidates/search")
def p1_search_candidates(payload: P1CandidateSearchRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.material_candidates.search(
            scope_id=payload.scope_id,
            speed_stage=payload.speed_stage,
            target_duration_min=payload.target_duration_min,
            target_duration_max=payload.target_duration_max,
            max_results=payload.max_results,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/p1/material-candidates/{candidate_id}/prepare")
def p1_prepare_candidate(candidate_id: str, payload: P1PrepareRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.material_preparation.prepare(
            candidate_id, payload.scope_id, payload.idempotency_key
        )
    except MaterialSelectionError as exc:
        raise _p1_error(exc) from exc


@router.post("/p1/difficulty/weekly-gate")
def p1_weekly_gate(payload: P1WeeklyGateRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.difficulty.evaluate_weekly_gate(payload.scope_id, payload.training_week_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/p1/difficulty/profile")
def p1_difficulty_profile(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.difficulty.get_profile(scope_id)


@router.get("/p1/difficulty/prompt")
def p1_difficulty_prompt(request: Request, scope_id: str = "default") -> dict[str, object]:
    prompt = request.app.state.difficulty.current_prompt(scope_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="no pending prompt")
    return prompt


@router.post("/p1/difficulty/upgrade-decision")
def p1_upgrade_decision(payload: P1UpgradeDecisionRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.difficulty.decide_upgrade(
            payload.scope_id, payload.prompt_id, payload.decision, payload.idempotency_key
        )
    except DifficultyError as exc:
        raise _p1_error(exc) from exc


# ================= P2: dashboard / memory / difficulty history =================

@router.get("/p2/dashboard")
def p2_dashboard(
    request: Request,
    scope_id: str = "default",
    range_start: str | None = None,
    range_end: str | None = None,
    granularity: str = "week",
) -> dict[str, object]:
    try:
        return request.app.state.dashboard.read(
            scope_id=scope_id, range_start=range_start, range_end=range_end, granularity=granularity
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/p2/memory/backfill")
def p2_memory_backfill(request: Request, scope_id: str = "default") -> dict[str, object]:
    created = request.app.state.memory.build_episodes(scope_id)
    return {"scope_id": scope_id, "episodes_created": created, "backfill": "additive"}


@router.get("/p2/memory")
def p2_memory(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.memory.read_memory(scope_id)


@router.get("/p2/memory/config")
def p2_memory_config_get(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.memory.get_config(scope_id)


@router.put("/p2/memory/config")
def p2_memory_config_put(payload: P2MemoryConfigRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.memory.save_config(
            payload.scope_id, short_days=payload.short_days, long_days=payload.long_days,
            min_episodes=payload.min_episodes, min_dates=payload.min_dates,
        )
    except MemoryConfigError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/p2/memory/suggestions")
def p2_memory_suggestions(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.memory.generate_suggestions(scope_id)


@router.post("/p2/memory/suggestions/action")
def p2_memory_suggestions_action(payload: P2SuggestionActionRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.memory.update_preferences(
            payload.scope_id, action=payload.action, target=payload.target
        )
    except MemoryConfigError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/p2/difficulty/history")
def p2_difficulty_history(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.difficulty_history.history(scope_id)


@router.post("/p2/difficulty/downgrade/suggest")
def p2_downgrade_suggest(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.difficulty_history.check_downgrade_suggestion(scope_id)


@router.post("/p2/difficulty/downgrade/request")
def p2_downgrade_request(request: Request, scope_id: str = "default") -> dict[str, object]:
    try:
        return request.app.state.difficulty_history.downgrade_request(scope_id)
    except DifficultyHistoryError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/p2/difficulty/downgrade/confirm")
def p2_downgrade_confirm(request: Request, scope_id: str = "default") -> dict[str, object]:
    try:
        return request.app.state.difficulty_history.downgrade_confirm(scope_id)
    except DifficultyHistoryError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/p2/difficulty/downgrade/decline")
def p2_downgrade_decline(request: Request, scope_id: str = "default") -> dict[str, object]:
    return request.app.state.difficulty_history.downgrade_decline(scope_id)


@router.get("/materials/{material_id}/progress")
def get_progress(material_id: str, request: Request) -> dict[str, object]:
    try:
        snapshot = request.app.state.progress_store.get(material_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return progress_payload(snapshot)


def _event_result(action) -> dict[str, object]:
    try:
        return progress_payload(action())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/materials/{material_id}/first-listen/complete")
def complete_first_listen(material_id: str, request: Request) -> dict[str, object]:
    return _event_result(lambda: request.app.state.training_events.complete_first_listen(material_id))


@router.post("/materials/{material_id}/comprehension-check")
def submit_comprehension(
    material_id: str, payload: ComprehensionCheckRequest, request: Request
) -> dict[str, object]:
    return _event_result(
        lambda: request.app.state.training_events.submit_comprehension(
            material_id=material_id,
            phase=payload.phase,
            self_rating=payload.self_rating,
            summary=payload.summary,
        )
    )


@router.post("/materials/{material_id}/dictation-parts/{part_no}/complete")
def complete_dictation_part(material_id: str, part_no: int, request: Request) -> dict[str, object]:
    return _event_result(
        lambda: request.app.state.training_events.complete_dictation_part(material_id, part_no)
    )


@router.post("/materials/{material_id}/second-listen/complete")
def complete_second_listen(material_id: str, request: Request) -> dict[str, object]:
    return _event_result(lambda: request.app.state.training_events.complete_second_listen(material_id))


@router.post("/materials/{material_id}/reading-parts/{part_no}/complete")
def complete_reading_part(material_id: str, part_no: int, request: Request) -> dict[str, object]:
    return _event_result(
        lambda: request.app.state.training_events.complete_reading_part(material_id, part_no)
    )


@router.post("/materials/{material_id}/full-reading-assessment")
def complete_full_reading_assessment(
    material_id: str, payload: ReadingAssessmentRequest, request: Request
) -> dict[str, object]:
    return _event_result(
        lambda: request.app.state.training_events.complete_full_reading_assessment(
            material_id,
            payload.passed,
            reference_duration=payload.reference_duration,
            user_duration=payload.user_duration,
            speed_result=payload.speed_result,
            pause_result=payload.pause_result,
            stress_result=payload.stress_result,
        )
    )


@router.post("/materials/{material_id}/reading/skip")
def skip_reading(material_id: str, request: Request) -> dict[str, object]:
    return _event_result(lambda: request.app.state.training_events.skip_reading(material_id))


@router.post("/time-logs/start")
def start_time_log(payload: TimeLogStartRequest, request: Request) -> dict[str, object]:
    try:
        return request.app.state.learning_time.start(**payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/time-logs/{time_log_id}/stop")
def stop_time_log(
    time_log_id: str, payload: TimeLogStopRequest, request: Request
) -> dict[str, object]:
    try:
        return request.app.state.learning_time.stop(time_log_id, payload.active_seconds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/home/recommendation")
def home_recommendation(request: Request) -> dict[str, object]:
    """Read-only homepage recommendation ladder (Spec 26.2)."""
    return request.app.state.home_recommendation.read()


@router.get("/stats")
def get_learning_stats(request: Request) -> dict[str, object]:
    return request.app.state.learning_time.stats()


@router.get("/weekly-assessments")
def list_weekly_assessments(request: Request) -> list[dict[str, object]]:
    return request.app.state.weekly_assessments.list()


@router.get("/weekly-assessments/{week_id}")
def get_weekly_assessment(week_id: str, request: Request) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.get(week_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/weekly-assessments")
def create_weekly_assessment(
    payload: WeeklyAssessmentCreateRequest, request: Request
) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.create(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/dictation")
def record_weekly_dictation(
    week_id: str, payload: WeeklyDictationRequest, request: Request
) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.record_dictation(
            week_id, payload.score, payload.passed
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/reading")
def record_weekly_reading(
    week_id: str, payload: WeeklyReadingRequest, request: Request
) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.record_reading(week_id, payload.dimensions)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/evaluate")
def evaluate_weekly_assessment(week_id: str, request: Request) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.evaluate(week_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/test-items")
def create_weekly_test_items(
    week_id: str,
    request: Request,
    payload: WeeklyTestItemsRequest | None = None,
) -> list[dict[str, object]]:
    count = payload.count if payload is not None else None
    try:
        return request.app.state.weekly_assessments.create_test_items(week_id, count)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/weekly-assessments/{week_id}/test-items")
def list_weekly_test_items(
    week_id: str, request: Request, kind: str = "TEST"
) -> list[dict[str, object]]:
    try:
        return request.app.state.weekly_assessments.list_test_items(week_id, kind)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/test-items/{item_id}/dictation")
def submit_weekly_test_dictation(
    week_id: str, item_id: str, payload: WeeklyTestItemDictationRequest, request: Request
) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.submit_test_dictation(
            week_id, item_id, payload.user_text, payload.listen_count
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/retest/confirm")
def confirm_weekly_retest(week_id: str, request: Request) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.confirm_retest(week_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/reinforcement/start")
def start_weekly_reinforcement(week_id: str, request: Request) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.start_reinforcement(week_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/weekly-assessments/{week_id}/reinforcement/items/{item_id}/dictation")
def submit_weekly_reinforcement_dictation(
    week_id: str, item_id: str, payload: WeeklyTestItemDictationRequest, request: Request
) -> dict[str, object]:
    try:
        return request.app.state.weekly_assessments.submit_reinforcement_dictation(
            week_id, item_id, payload.user_text, payload.listen_count
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/materials/{material_id}/dictation-context")
def get_dictation_context(material_id: str, request: Request) -> dict[str, object]:
    try:
        return request.app.state.dictation_service.get_context(material_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/materials/{material_id}/reading-parts/{part_no}/score")
def score_reading_part(
    material_id: str, part_no: int, payload: RecordingScoreRequest, request: Request
) -> dict[str, object]:
    try:
        recording_path = request.app.state.reading_service.save_recording(
            payload.filename, payload.content_base64
        )
        return request.app.state.reading_service.score(
            material_id=material_id,
            scope="PART",
            part_no=part_no,
            recording_path=recording_path,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/materials/{material_id}/full-reading/score")
def score_full_reading(
    material_id: str, payload: RecordingScoreRequest, request: Request
) -> dict[str, object]:
    try:
        recording_path = request.app.state.reading_service.save_recording(
            payload.filename, payload.content_base64
        )
        return request.app.state.reading_service.score(
            material_id=material_id,
            scope="FULL",
            part_no=None,
            recording_path=recording_path,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/materials/{material_id}/sentences/{sentence_id}/dictation")
def submit_dictation(
    material_id: str,
    sentence_id: str,
    payload: DictationSubmitRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return request.app.state.dictation_service.submit(
            material_id=material_id,
            sentence_id=sentence_id,
            user_text=payload.user_text,
            listen_count=payload.listen_count,
            hint_level=payload.hint_level,
            revealed=payload.revealed,
            memory_targets=payload.memory_targets,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
