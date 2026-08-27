"""Transcript validation for material candidates (P1 3.1).

The transcript must exist and correspond to the audio. VOA's official
transcript is client-side only, so candidates carry an ASR-generated
transcript; its completeness is judged by sentence coverage of the audio
timeline, and mismatches (garbled/empty output) are rejected.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


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


def encode_timestamped(sentences: list[tuple[str, float, float]]) -> str:
    return json.dumps([{"text": t, "start_time": s, "end_time": e} for t, s, e in sentences])


def decode_timestamped(payload: str | None) -> list[tuple[str, float, float]]:
    if not payload:
        return []
    return [
        (item["text"], float(item["start_time"]), float(item["end_time"]))
        for item in json.loads(payload)
    ]
