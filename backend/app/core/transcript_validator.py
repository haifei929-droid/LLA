"""Transcript validation for material candidates (P1 3.1).

The transcript must exist and correspond to the audio. VOA's official
transcript is client-side only, so candidates carry an ASR-generated
transcript; its completeness is judged by sentence coverage of the audio
timeline, and mismatches (garbled/empty output) are rejected.

Timeline coverage is not transcript *verification*: machine alignment (or a
high ASR similarity score) can never promote a transcript to verified, because
the timestamps only prove that the sentence layout covers the audio. Only an
official transcript with declared evidence is verifiable; anything else stays
pending human review.

A declaration is also not evidence that the official script matches the audio:
when an independent ASR run contradicts the official transcript, the entry is
CONTRADICTED (pending review), never VERIFIED. A fixture where the official
text and the ASR output are unrelated content is exactly that case.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

#: Transcript evidence states consumed by the source-governance service.
TRANSCRIPT_VERIFIED = "VERIFIED"
TRANSCRIPT_PENDING_REVIEW = "PENDING_REVIEW"
TRANSCRIPT_REJECTED = "REJECTED"
OFFICIAL_ORIGIN = "OFFICIAL"
CONTRADICTED = "CONTRADICTED"

#: Minimum word-set agreement between the official transcript and an
#: independent ASR pass. Below this the two sources describe different content.
OFFICIAL_ASR_MIN_AGREEMENT = 0.60

_WORD = re.compile(r"[a-z0-9']+")


def transcript_agreement(official_text: str | None, asr_text: str | None) -> float | None:
    """Word-set agreement between the official script and the ASR output.

    Returns None when either side has no words (no comparison is possible), so
    the caller can distinguish "not compared" from "compared and divergent".
    """
    official_words = set(_WORD.findall(str(official_text or "").lower()))
    asr_words = set(_WORD.findall(str(asr_text or "").lower()))
    if not official_words or not asr_words:
        return None
    return len(official_words & asr_words) / len(official_words | asr_words)


@dataclass(frozen=True)
class TranscriptValidation:
    status: str  # COMPLETE | INCOMPLETE | MISSING | MISMATCHED
    sentence_count: int
    coverage_ratio: float  # fraction of audio duration covered by sentence timestamps
    reason: str | None = None


def validate_transcript(
    transcript: str,
    timestamped_sentences: list[tuple[str, float, float]] | None,
    audio_duration: float,
    min_coverage: float = 0.80,
    min_sentences: int = 10,
) -> TranscriptValidation:
    if not transcript or not transcript.strip():
        return TranscriptValidation("MISSING", 0, 0.0, "transcript is empty")

    sentences = timestamped_sentences or []
    if not sentences:
        return TranscriptValidation("MISMATCHED", 0, 0.0, "no aligned sentence timestamps")

    last_end = max(end for _, _, end in sentences)
    coverage = last_end / audio_duration if audio_duration > 0 else 0.0
    words = len(re.findall(r"\b[\w']+\b", transcript))
    if words < 20:
        return TranscriptValidation("INCOMPLETE", len(sentences), coverage, "transcript too short")

    if coverage < min_coverage:
        return TranscriptValidation("INCOMPLETE", len(sentences), coverage, "timeline coverage below threshold")

    if len(sentences) < min_sentences:
        return TranscriptValidation("INCOMPLETE", len(sentences), coverage, "too few sentences")

    return TranscriptValidation("COMPLETE", len(sentences), coverage, None)


def transcript_verification(
    validation: TranscriptValidation,
    transcript_origin: str | None,
    declared_verified: bool = False,
    agreement: float | None = None,
) -> str:
    """Decide the transcript evidence state for a candidate.

    - a failed validation is rejected outright;
    - an official script that an independent ASR pass contradicts is
      CONTRADICTED: a boolean declaration never replaces the agreement check;
    - ASR-generated text is never verified by alignment/coverage alone;
    - an official transcript still needs declared (human) evidence before it
      counts as verified.
    """
    if validation.status != "COMPLETE":
        return TRANSCRIPT_REJECTED
    if str(transcript_origin or "").upper() != OFFICIAL_ORIGIN:
        return TRANSCRIPT_PENDING_REVIEW
    if agreement is not None and agreement < OFFICIAL_ASR_MIN_AGREEMENT:
        return CONTRADICTED
    if not declared_verified:
        return TRANSCRIPT_PENDING_REVIEW
    return TRANSCRIPT_VERIFIED


def encode_timestamped(sentences: list[tuple[str, float, float]]) -> str:
    return json.dumps([{"text": t, "start_time": s, "end_time": e} for t, s, e in sentences])


def decode_timestamped(payload: str | None) -> list[tuple[str, float, float]]:
    if not payload:
        return []
    return [
        (item["text"], float(item["start_time"]), float(item["end_time"]))
        for item in json.loads(payload)
    ]
