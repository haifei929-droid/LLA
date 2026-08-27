from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class TimestampedSentenceInput(BaseModel):
    text: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)


class MaterialCreateRequest(BaseModel):
    material_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    audio_path: str = Field(min_length=1)
    transcript: str = Field(min_length=1)
    timestamped_sentences: list[TimestampedSentenceInput] = Field(min_length=3)
    #: Optional 1-based sentence indexes of natural semantic part boundaries.
    natural_part_boundaries: list[int] | None = None


class DictationSubmitRequest(BaseModel):
    user_text: str = ""
    listen_count: int = Field(ge=1)
    hint_level: int = Field(default=0, ge=0, le=2)
    revealed: bool = False
    memory_targets: list[str] = Field(default_factory=list)


class ComprehensionCheckRequest(BaseModel):
    phase: Literal["FIRST", "SECOND"]
    self_rating: Literal["<30%", "30–50%", "50–70%", ">70%"]
    summary: str = Field(min_length=1)


class ReadingAssessmentRequest(BaseModel):
    passed: bool
    reference_duration: float | None = Field(default=None, ge=0)
    user_duration: float | None = Field(default=None, ge=0)
    speed_result: str | None = None
    pause_result: str | None = None
    stress_result: str | None = None


class TimeLogStartRequest(BaseModel):
    activity_type: str = Field(min_length=1)
    material_id: str | None = None
    session_id: str | None = None


class TimeLogStopRequest(BaseModel):
    active_seconds: int = Field(ge=0)


class WeeklyAssessmentCreateRequest(BaseModel):
    week_id: str = Field(min_length=1)
    period_start: str = Field(min_length=1)
    period_end: str = Field(min_length=1)
    dictation_required: bool = True
    reading_required: bool = True


class WeeklyDictationRequest(BaseModel):
    score: float = Field(ge=0, le=100)
    passed: bool


class WeeklyReadingRequest(BaseModel):
    dimensions: dict[str, bool] = Field(min_length=1)


class WeeklyTestItemDictationRequest(BaseModel):
    user_text: str = ""
    listen_count: int = Field(ge=1)


class WeeklyTestItemsRequest(BaseModel):
    count: int | None = Field(default=None, ge=1)


class RecordingScoreRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)


class P1CandidateSearchRequest(BaseModel):
    scope_id: str = Field(default="default")
    speed_stage: str = "STAGE_1"
    target_duration_min: float = Field(default=15.0, ge=1)
    target_duration_max: float = Field(default=20.0, ge=1)
    max_results: int = Field(default=3, ge=1, le=3)


class P1PrepareRequest(BaseModel):
    scope_id: str = Field(default="default")
    idempotency_key: str = Field(min_length=1)


class P1WeeklyGateRequest(BaseModel):
    scope_id: str = Field(default="default")
    training_week_id: str = Field(min_length=1)


class P1UpgradeDecisionRequest(BaseModel):
    scope_id: str = Field(default="default")
    decision: str
    prompt_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
