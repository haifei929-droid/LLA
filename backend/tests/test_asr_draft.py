"""ASR drafts are a starting point for review, never the dictation answer key."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import asr_draft


def test_merge_segments_joins_mid_sentence_splits(monkeypatch) -> None:
    raw = [
        {"text": " My name is", "start": 0.0, "end": 1.0},
        {"text": "John.", "start": 1.0, "end": 2.0},
        {"text": "London is in", "start": 2.2, "end": 3.0},
        {"text": "Great Britain.", "start": 3.0, "end": 4.5},
    ]
    merged = asr_draft.merge_segments(raw)
    assert [item["text"] for item in merged] == ["My name is John.", "London is in Great Britain."]
    assert merged[0]["start"] == 0.0
    assert merged[1]["end"] == 4.5


def test_draft_transcript_reports_missing_package(monkeypatch) -> None:
    monkeypatch.setattr(asr_draft, "asr_available", lambda: False)
    with pytest.raises(asr_draft.ASRUnavailable) as excinfo:
        asr_draft.draft_transcript(Path("x.mp3"))
    assert "not installed" in str(excinfo.value)


def test_write_draft_never_overwrites_a_human_transcript(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "ENEN001.mp3"
    audio.write_bytes(b"\x00")
    corrected = tmp_path / "ENEN001.txt"
    corrected.write_text("Human corrected line.\n", encoding="utf-8")

    monkeypatch.setattr(
        asr_draft,
        "draft_transcript",
        lambda *a, **k: {
            "model": "base",
            "language": "en",
            "duration": 3.0,
            "segments": [{"text": "machine line.", "start": 0.0, "end": 3.0}],
            "text": "machine line.",
        },
    )
    result = asr_draft.write_draft(audio)

    assert result["text_written"] is False
    assert corrected.read_text(encoding="utf-8") == "Human corrected line.\n"
    payload = json_local = (tmp_path / "ENEN001.asr.json").read_text(encoding="utf-8")
    assert "machine line." in payload

def test_strip_wrappers_drops_the_spoken_intro_and_outro() -> None:
    segments = [
        {"text": "50 languages.", "start": 0.0, "end": 1.0},
        {"text": "Lesson 5 countries and languages.", "start": 1.0, "end": 3.0},
        {"text": "John is from London.", "start": 3.0, "end": 5.0},
        {"text": "London is in Great Britain.", "start": 5.0, "end": 7.0},
        {"text": "Please visit www.book2.de for books and texts.", "start": 7.0, "end": 9.0},
    ]
    kept, intro, outro = asr_draft.strip_wrappers(segments)
    assert [item["text"] for item in kept] == [
        "John is from London.",
        "London is in Great Britain.",
    ]
    assert (intro, outro) == (2, 1)


def test_collapse_doubling_counts_the_second_voice() -> None:
    segments = [
        {"text": "John is from London", "start": 0.0, "end": 2.0},
        {"text": "john is from london", "start": 2.0, "end": 4.0},
        {"text": "London is in Great Britain", "start": 4.0, "end": 7.0},
    ]
    phrases = asr_draft.collapse_doubling(segments)
    assert len(phrases) == 2
    assert phrases[0]["repeats"] == 2
    assert phrases[0]["start"] == 0.0 and phrases[0]["end"] == 4.0
    assert phrases[1]["repeats"] == 1


def test_merge_does_not_swallow_a_whole_lesson() -> None:
    # Phrases that end on content words must stay separate even without a full stop.
    segments = [
        {"text": "John is from London", "start": 0.0, "end": 2.0},
        {"text": "Maria is from Madrid", "start": 2.0, "end": 4.0},
        {"text": "The family", "start": 4.0, "end": 5.0},
        {"text": "is big", "start": 5.0, "end": 6.0},
    ]
    merged = asr_draft.merge_segments(segments)
    assert [item["text"] for item in merged] == [
        "John is from London",
        "Maria is from Madrid",
        "The family is big",
    ]
