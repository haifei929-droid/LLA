"""Forced alignment: put a human-corrected transcript onto the audio's timeline.

ASR drafts give good *words* on some lessons and good *timing* on others, rarely
both, and book2 reads every phrase twice with two voices. Proportional timing
(word count over duration) is a guess; this module replaces the guess with a
measurement: align the corrected text word by word against the ASR word
timestamps, then read each line's boundaries off the matched words.

Rules that matter more than the algorithm:

* a line whose words do not match the audio well enough is reported as
  ``unaligned`` - never assigned a fabricated boundary;
* a repeated reading (male then female) extends the line's end and is counted in
  ``repeats``, because the dictation answer is one line but the audio says it
  twice;
* intro/outro words simply stay unmatched; they belong to no line.

The caller decides what to do with unaligned lines (refuse the import, or send
them back for review).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

_PUNCTUATION = re.compile(r"[^a-z0-9']")


def normalise_word(value: str) -> str:
    return _PUNCTUATION.sub("", str(value).lower())


@dataclass
class AlignedLine:
    text: str
    start: float
    end: float
    repeats: int = 1
    coverage: float = 0.0


@dataclass
class Alignment:
    lines: list[AlignedLine] = field(default_factory=list)
    unaligned: list[dict[str, Any]] = field(default_factory=list)
    coverage: float = 0.0
    matched_words: int = 0
    total_words: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": [
                {
                    "text": line.text,
                    "start": round(line.start, 3),
                    "end": round(line.end, 3),
                    "repeats": line.repeats,
                    "coverage": round(line.coverage, 3),
                }
                for line in self.lines
            ],
            "unaligned": self.unaligned,
            "coverage": round(self.coverage, 3),
            "matched_words": self.matched_words,
            "total_words": self.total_words,
        }


def _similarity(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(a=list(left), b=list(right), autojunk=False).ratio()


def align_lines(
    lines: Iterable[str],
    words: Sequence[dict[str, Any]],
    *,
    min_coverage: float = 0.6,
    repeat_similarity: float = 0.7,
) -> Alignment:
    """Align transcript lines to word timestamps.

    ``words`` is ``[{"word": str, "start": float, "end": float}, ...]`` as the
    speech model reports it. Returns an :class:`Alignment`; lines below
    ``min_coverage`` land in ``unaligned`` instead of receiving invented times.
    """
    lines = [str(line).strip() for line in lines if str(line).strip()]
    flat = [
        (normalise_word(item.get("word", "")), float(item.get("start", 0.0)), float(item.get("end", 0.0)))
        for item in words
    ]
    flat = [item for item in flat if item[0]]
    if not lines or not flat:
        return Alignment(
            unaligned=[{"line": index, "text": line, "coverage": 0.0} for index, line in enumerate(lines)],
            total_words=sum(len(line.split()) for line in lines),
        )

    target: list[tuple[int, str]] = []
    line_ranges: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        tokens = [normalise_word(token) for token in line.split()]
        tokens = [token for token in tokens if token]
        start_index = len(target)
        target.extend((index, token) for token in tokens)
        line_ranges.append((start_index, len(target)))
    target_words = [token for _, token in target]
    asr_words = [item[0] for item in flat]

    matcher = difflib.SequenceMatcher(a=target_words, b=asr_words, autojunk=False)
    matched: dict[int, int] = {}
    for a_start, b_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            matched[a_start + offset] = b_start + offset

    alignment = Alignment(total_words=len(target_words))
    for line_index, (start_index, end_index) in enumerate(line_ranges):
        positions = [matched[index] for index in range(start_index, end_index) if index in matched]
        expected = max(1, end_index - start_index)
        coverage = len(positions) / expected
        if not positions or coverage < min_coverage:
            alignment.unaligned.append(
                {"line": line_index, "text": lines[line_index], "coverage": round(coverage, 3)}
            )
            continue

        first, last = min(positions), max(positions)
        start = flat[first][1]
        end = flat[last][2]
        repeats = 1

        # A second reading of the same phrase follows immediately (male/female).
        line_words = target_words[start_index:end_index]
        following = asr_words[last + 1 : last + 1 + len(line_words)]
        if following and _similarity(line_words, following) >= repeat_similarity:
            end = flat[min(len(flat) - 1, last + len(following))][2]
            repeats = 2

        alignment.lines.append(
            AlignedLine(
                text=lines[line_index],
                start=start,
                end=end,
                repeats=repeats,
                coverage=round(coverage, 3),
            )
        )
        alignment.matched_words += len(positions)

    # Matched words can straddle a boundary by a few milliseconds; a dictation
    # timeline must not have two lines claiming the same audio.
    cursor = 0.0
    for line in alignment.lines:
        line.start = max(line.start, cursor)
        if line.end <= line.start:
            line.end = round(line.start + 0.01, 3)
        cursor = line.end

    alignment.coverage = (
        round(alignment.matched_words / alignment.total_words, 3) if alignment.total_words else 0.0
    )
    return alignment
