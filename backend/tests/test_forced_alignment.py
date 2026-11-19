"""Forced alignment: boundaries come from measured words, never from guessing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.forced_alignment import align_lines

from tests.fixtures import make_database, make_settings


def words_for(text: str, start: float = 0.0, step: float = 0.5) -> list[dict]:
    words: list[dict] = []
    cursor = start
    for token in text.split():
        words.append({"word": token, "start": round(cursor, 2), "end": round(cursor + step, 2)})
        cursor += step
    return words


def test_alignment_reads_boundaries_off_the_matched_words() -> None:
    lines = ["John is from London", "London is in Great Britain"]
    words = words_for("John is from London London is in Great Britain")
    alignment = align_lines(lines, words)

    assert [line.text for line in alignment.lines] == lines
    assert alignment.lines[0].start == 0.0
    assert alignment.lines[0].end == pytest.approx(2.0)
    assert alignment.lines[1].start == pytest.approx(2.0)
    assert alignment.lines[1].end == pytest.approx(4.5)
    assert alignment.unaligned == []
    assert alignment.coverage == pytest.approx(1.0)


def test_a_second_reading_extends_the_line_and_is_counted() -> None:
    lines = ["John is from London"]
    words = words_for("John is from London John is from London")
    alignment = align_lines(lines, words)

    assert len(alignment.lines) == 1
    line = alignment.lines[0]
    assert line.repeats == 2
    assert line.start == 0.0
    assert line.end == pytest.approx(4.0), "the timeline must cover both voices"


def test_a_misheard_word_still_aligns() -> None:
    lines = ["Maria is from Madrid"]
    words = words_for("Maria is from Madrid")
    words[1]["word"] = "isis"  # one garbled token
    alignment = align_lines(lines, words)

    assert len(alignment.lines) == 1
    assert alignment.lines[0].coverage >= 0.6


def test_an_unmatched_line_is_reported_not_invented() -> None:
    lines = ["Completely unrelated sentence about penguins"]
    words = words_for("John is from London London is in Great Britain")
    alignment = align_lines(lines, words)

    assert alignment.lines == []
    assert len(alignment.unaligned) == 1
    assert alignment.unaligned[0]["line"] == 0
    assert alignment.unaligned[0]["coverage"] == 0.0


def test_intro_and_outro_words_are_simply_unmatched() -> None:
    lines = ["John is from London"]
    words = words_for("50 languages lesson 5 John is from London visit book2 dot de")
    alignment = align_lines(lines, words)

    assert len(alignment.lines) == 1
    assert alignment.lines[0].start >= 1.0, "the line starts after the intro words"
    # Coverage counts transcript words that found a home, so it is 1.0 here;
    # what proves the wrappers were ignored is that fewer words matched than exist.
    assert alignment.matched_words == 4


def _batch_with_alignment(tmp_path: Path, lines: list[str], starts: list[tuple[float, float]]) -> Path:
    batch = tmp_path / "inbox" / "b1"
    batch.mkdir(parents=True)
    (batch / "batch.json").write_text(
        json.dumps({"source_platform": "50LANGUAGES", "source_course": "book2-en-uk",
                    "license_status": "BY-NC-ND-3.0-US"}),
        encoding="utf-8",
    )
    (batch / "ENEN001.mp3").write_bytes(b"\x00" * 8)
    (batch / "ENEN001.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    aligned = {
        "audio": "ENEN001.mp3",
        "model": "test",
        "lines": [
            {"text": text, "start": start, "end": end, "repeats": 1, "coverage": 1.0}
            for text, (start, end) in zip(lines, starts)
        ],
        "unaligned": [],
        "coverage": 1.0,
    }
    (batch / "ENEN001.aligned.json").write_text(json.dumps(aligned), encoding="utf-8")
    return batch


def test_import_prefers_aligned_boundaries(tmp_path: Path) -> None:
    from app.core.local_import import LocalImportService

    lines = ["John is from London", "London is in Great Britain", "He speaks English"]
    batch = _batch_with_alignment(tmp_path, lines, [(3.0, 5.5), (6.0, 9.0), (9.5, 12.0)])
    database = make_database(tmp_path)
    service = LocalImportService(database, make_settings(tmp_path), probe=lambda _p: 30.0)

    result = service.import_batch(batch)
    material_id = result["imported"][0]["material_id"]

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT text, start_time, end_time FROM sentences WHERE material_id = ? ORDER BY sequence_no",
            (material_id,),
        ).fetchall()
        evidence = connection.execute(
            "SELECT difficulty_evidence FROM materials WHERE material_id = ?", (material_id,)
        ).fetchone()["difficulty_evidence"]

    assert [(row["start_time"], row["end_time"]) for row in rows] == [(3.0, 5.5), (6.0, 9.0), (9.5, 12.0)]
    assert "forced-word-alignment" in evidence


def test_import_refuses_a_transcript_with_unaligned_lines(tmp_path: Path) -> None:
    from app.core.local_import import LocalImportService

    lines = ["John is from London"]
    batch = _batch_with_alignment(tmp_path, lines, [(3.0, 5.5)])
    aligned = json.loads((batch / "ENEN001.aligned.json").read_text(encoding="utf-8"))
    aligned["unaligned"] = [{"line": 0, "text": lines[0], "coverage": 0.1}]
    (batch / "ENEN001.aligned.json").write_text(json.dumps(aligned), encoding="utf-8")

    database = make_database(tmp_path)
    service = LocalImportService(database, make_settings(tmp_path), probe=lambda _p: 30.0)
    result = service.import_batch(batch)

    assert result["imported"] == []
    assert result["failed"] and "alignment" in str(result["failed"][0]["reason"])
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"] == 0

def test_consecutive_lines_never_overlap() -> None:
    lines = ["John is from London", "London is in Great Britain"]
    words = words_for("John is from London London is in Great Britain")
    alignment = align_lines(lines, words)
    for previous, current in zip(alignment.lines, alignment.lines[1:]):
        assert current.start >= previous.end, "a line must not claim audio the previous line already used"
