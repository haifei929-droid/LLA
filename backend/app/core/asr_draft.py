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


def merge_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge over-split ASR segments into sentence-ish units.

    Whisper sometimes emits a new segment mid-sentence. Joining a segment with
    the previous one while the previous text does not end with terminal
    punctuation keeps sentences whole without throwing away the timestamps.
    """
    merged: list[dict[str, Any]] = []
    for segment in segments:
        text = _clean(str(segment.get("text", "")))
        if not text:
            continue
        if merged and not re.search(r"[.!?]['\"]?$", str(merged[-1]["text"])):
            merged[-1]["text"] = _clean(f"{merged[-1]['text']} {text}")
            merged[-1]["end"] = float(segment.get("end", merged[-1]["end"]))
        else:
            merged.append(
                {
                    "text": text,
                    "start": float(segment.get("start", 0.0)),
                    "end": float(segment.get("end", 0.0)),
                }
            )
    return merged


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
    if not merged:
        raise ASRUnavailable(f"no speech recognised in {audio_path.name}")
    return {
        "model": model_size,
        "language": language,
        "duration": round(float(getattr(info, "duration", 0.0)), 3),
        "segments": merged,
        "text": " ".join(segment["text"] for segment in merged),
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
        text_path.write_text("\n".join(segment["text"] for segment in draft["segments"]) + "\n", encoding="utf-8")
        written_text = True
    json_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "audio": audio_path.name,
        "model": draft["model"],
        "segments": len(draft["segments"]),
        "duration": draft["duration"],
        "text_written": written_text,
        "txt": text_path.name,
        "asr_json": json_path.name,
    }
