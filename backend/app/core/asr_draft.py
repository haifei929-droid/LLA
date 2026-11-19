"""ASR drafts: machine transcript as a *starting point*, never as the answer key.

The pool's dictation judgement is "exactly right, character for character", so
the standard answer has to be trustworthy. A speech model's output is not: it
mishears short phrases, drops articles and invents punctuation. Therefore an ASR
draft may

* give the material an initial transcript so the lesson can be reviewed,
* give real sentence timestamps (much better than proportional timing),

and may **not** be treated as a verified transcript. The draft is written next to
the audio (``<stem>.txt`` + ``<stem>.asr.json``) for the owner to correct; the
import path keeps ``transcript_verified = 0`` until a human approves it.

This module never downloads anything on its own: the model is loaded lazily and a
missing model surfaces as :class:`ASRUnavailable` with an actionable message.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "base"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class ASRUnavailable(RuntimeError):
    """Raised when faster-whisper (or its model) cannot be used."""


def asr_available() -> bool:
    """True when the optional ``faster-whisper`` package is importable."""
    return importlib.util.find_spec("faster_whisper") is not None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_TERMINAL = re.compile(r"[.!?]['\"]?$")
#: A segment that ends on one of these is a fragment, not a finished sentence.
_DANGLING_TAIL = frozenset(
    "is are was were be been am the a an of in on at to from and or but my your his her their "
    "our that this these those with for as do does did can could will would shall should".split()
)


def merge_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join only the splits that are clearly mid-sentence.

    The first version merged whenever the previous segment lacked terminal
    punctuation, which swallowed whole lessons into one 100-second segment on
    material whose phrases are short and sparsely punctuated. A merge now also
    requires the continuation to start lower-case and the previous part to be
    short, so a missing full stop can never collapse a lesson again.
    """
    def _is_fragment(previous_text: str, continuation: str) -> bool:
        if _TERMINAL.search(previous_text):
            return False
        words = previous_text.split()
        if not words or len(words) >= 12:
            return False
        if continuation[:1].islower():
            return True
        return words[-1].lower().strip(",;:") in _DANGLING_TAIL

    merged: list[dict[str, Any]] = []
    for segment in segments:
        text = _clean(str(segment.get("text", "")))
        if not text:
            continue
        previous = merged[-1] if merged else None
        if previous is not None and _is_fragment(str(previous["text"]), text):
            previous["text"] = _clean(f"{previous['text']} {text}")
            previous["end"] = float(segment.get("end", previous["end"]))
        else:
            merged.append(
                {
                    "text": text,
                    "start": float(segment.get("start", 0.0)),
                    "end": float(segment.get("end", 0.0)),
                }
            )
    return merged


#: Spoken wrappers that are not part of the lesson text: the publisher's intro
#: ("50 languages, lesson N: title") and the closing pointer ("visit book2.de").
_INTRO_MARKERS = ("50 languages", "fifty languages", "audio course", "book2")
_OUTRO_MARKERS = ("book2", "wwwbook2", "for books and texts", "visit")


def strip_wrappers(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Drop the spoken intro and outro; return the kept segments and both counts."""
    def normalise(text: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", text.lower())

    def is_intro(text: str, index: int) -> bool:
        normalised = normalise(text)
        if any(marker in normalised for marker in _INTRO_MARKERS):
            return True
        # The spoken title ("lesson 5 countries and languages", "2. family members")
        # only ever appears in the first couple of segments; a phrase that merely
        # starts with a number deeper in the lesson is left alone.
        if index < 2 and (re.match(r"^\s*(lesson\s*)?\d+\b", normalised) or "lesson" in normalised):
            return True
        return False

    start = 0
    while start < len(segments) and is_intro(str(segments[start]["text"]), start):
        start += 1
    end = len(segments)
    while end > start and any(
        marker in normalise(str(segments[end - 1]["text"])) for marker in _OUTRO_MARKERS
    ):
        end -= 1
    return segments[start:end], start, len(segments) - end


def collapse_doubling(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive repeats: book2 reads each phrase with two voices.

    Returns phrases of ``{"text", "start", "end", "repeats"}`` where ``repeats``
    is how many consecutive segments said the same thing. This matters for
    dictation: the answer key is one line, but the audio says it twice, and the
    timestamps must cover both utterances.
    """
    phrases: list[dict[str, Any]] = []
    for segment in segments:
        key = re.sub(r"[^a-z0-9 ]", "", str(segment["text"]).lower()).strip()
        if phrases and key and re.sub(
            r"[^a-z0-9 ]", "", str(phrases[-1]["text"]).lower()
        ).strip() == key:
            phrases[-1]["end"] = float(segment["end"])
            phrases[-1]["repeats"] += 1
            continue
        phrases.append(
            {
                "text": segment["text"],
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "repeats": 1,
            }
        )
    return phrases


def draft_transcript(
    audio_path: Path,
    *,
    model_size: str = DEFAULT_MODEL,
    language: str = "en",
) -> dict[str, Any]:
    """Transcribe one audio file into sentence-ish segments.

    Returns ``{"model", "language", "segments", "text"}``. Raises
    :class:`ASRUnavailable` when the package or model cannot be loaded, so the
    caller can tell "not installed" apart from "this file failed".
    """
    if not asr_available():
        raise ASRUnavailable(
            "faster-whisper is not installed; install it or provide a transcript by hand"
        )
    try:
        from faster_whisper import WhisperModel
    except Exception as error:  # pragma: no cover - import failure is environment specific
        raise ASRUnavailable(f"cannot import faster-whisper: {error}") from error

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        raw_segments, info = model.transcribe(str(audio_path), language=language)
        segments = [
            {"text": segment.text, "start": segment.start, "end": segment.end}
            for segment in raw_segments
            if str(segment.text).strip()
        ]
    except Exception as error:  # pragma: no cover - depends on model download
        raise ASRUnavailable(f"speech model unavailable: {error}") from error

    merged = merge_segments(segments)
    kept, intro_dropped, outro_dropped = strip_wrappers(merged)
    if not kept:
        raise ASRUnavailable(f"no lesson speech recognised in {audio_path.name} after dropping the wrappers")
    phrases = collapse_doubling(kept)
    return {
        "model": model_size,
        "language": language,
        "duration": round(float(getattr(info, "duration", 0.0)), 3),
        "segments": kept,
        "phrases": phrases,
        "dropped_intro_segments": intro_dropped,
        "dropped_outro_segments": outro_dropped,
        "repeated_phrases": sum(1 for phrase in phrases if phrase["repeats"] > 1),
        "text": " ".join(phrase["text"] for phrase in phrases),
    }


def write_draft(audio_path: Path, *, model_size: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Write ``<stem>.txt`` and ``<stem>.asr.json`` next to the audio.

    Existing ``.txt`` files are never overwritten: a human-corrected transcript
    outranks a machine draft, always.
    """
    draft = draft_transcript(audio_path, model_size=model_size)
    text_path = audio_path.with_suffix(".txt")
    json_path = audio_path.with_suffix(".asr.json")
    written_text = False
    if not text_path.exists():
        lines = [phrase["text"] for phrase in draft.get("phrases") or draft["segments"]]
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written_text = True
    json_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "audio": audio_path.name,
        "model": draft["model"],
        "segments": len(draft["segments"]),
        "phrases": len(draft.get("phrases") or []),
        "repeated_phrases": draft.get("repeated_phrases", 0),
        "dropped_intro_segments": draft.get("dropped_intro_segments", 0),
        "dropped_outro_segments": draft.get("dropped_outro_segments", 0),
        "duration": draft["duration"],
        "text_written": written_text,
        "txt": text_path.name,
        "asr_json": json_path.name,
    }
