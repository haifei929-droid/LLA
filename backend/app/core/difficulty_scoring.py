"""Self-calibrated difficulty scoring for the material pool.

The pool does not trust a platform's own CEFR claim. Difficulty is measured from
what the material actually contains - sentence gaps, speech rate and sentence
length - all derivable from the aligned transcript that ingestion already writes.
No new dependency, no model, no external vocabulary list.

Scoring is deliberately boring and auditable: three normalised metrics, weighted,
monotone 0-100, mapped to a band. The thresholds are marked UNCALIBRATED until a
human scores 12-20 samples (docs/LLA_Material_Pool_Plan.md section 3). Every
stored score carries ``CALIBRATION_VERSION``, so two rulers are never silently
mixed inside one pool.
"""

from __future__ import annotations

from collections.abc import Sequence

CALIBRATION_VERSION = "v0-uncalibrated"

#: Reference ranges used to normalise each metric into 0..1. They describe the
#: shape of graded ESOL listening material, not the quirks of one platform.
WPM_RANGE = (70.0, 180.0)
MEAN_SENTENCE_RANGE = (4.0, 18.0)
GAP_RANGE = (0.15, 1.20)

WEIGHTS = {"wpm": 0.45, "sentence_length": 0.30, "pause": 0.25}

#: Upper bound is exclusive except for the last bucket.
BAND_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("A1", 0.0, 33.0),
    ("A2", 33.0, 58.0),
    ("B1", 58.0, 80.0),
    ("B2", 80.0, 100.1),
)

Span = tuple[str, float, float]


class DifficultyInputError(ValueError):
    """Raised when a material cannot be scored from its own sentences."""


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _normalise(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp01((value - low) / (high - low))


def _word_count(text: str) -> int:
    return len([chunk for chunk in str(text).replace("\n", " ").split(" ") if chunk.strip()])


def compute_metrics(spans: Sequence[Span], duration_seconds: float) -> dict[str, float]:
    """Measure the three difficulty inputs from aligned sentence spans.

    ``spans`` is a sequence of ``(text, start_seconds, end_seconds)``; that is
    exactly what the ``sentences`` table stores after ingestion.
    """
    if duration_seconds <= 0:
        raise DifficultyInputError("duration_seconds must be positive to score difficulty")
    usable = [(str(text), float(start), float(end)) for text, start, end in spans if str(text).strip()]
    if not usable:
        raise DifficultyInputError("material has no sentences to score")

    counts = [_word_count(text) for text, _start, _end in usable]
    total_words = sum(counts)
    if total_words == 0:
        raise DifficultyInputError("material transcript contains no word")

    gaps = sorted(max(0.0, usable[index + 1][1] - usable[index][2]) for index in range(len(usable) - 1))
    median_gap = gaps[len(gaps) // 2] if gaps else 0.0

    lengths = sorted(counts)
    p90_length = lengths[min(len(lengths) - 1, int(round(0.9 * (len(lengths) - 1))))]

    return {
        "wpm": total_words / (duration_seconds / 60.0),
        "mean_sentence_words": total_words / len(usable),
        "p90_sentence_words": float(p90_length),
        "median_gap_seconds": median_gap,
        "sentence_count": float(len(usable)),
        "word_count": float(total_words),
        "duration_seconds": float(duration_seconds),
    }


def score_from_metrics(metrics: dict[str, float]) -> float:
    """Weighted 0-100 difficulty score (monotone in every input)."""
    parts = {
        "wpm": _normalise(metrics["wpm"], *WPM_RANGE),
        "sentence_length": _normalise(metrics["mean_sentence_words"], *MEAN_SENTENCE_RANGE),
        "pause": _normalise(metrics["median_gap_seconds"], *GAP_RANGE),
    }
    raw = sum(WEIGHTS[name] * value for name, value in parts.items())
    return round(100.0 * raw, 2)


def band_for_score(score: float) -> str:
    """Map a 0-100 score to a band; the last bucket absorbs clamp overflow."""
    for band, low, high in BAND_BOUNDS:
        if low <= score < high:
            return band
    return BAND_BOUNDS[-1][0]


def band_bounds(band: str) -> tuple[float, float]:
    """Inclusive lower / exclusive upper score bound for a band."""
    for name, low, high in BAND_BOUNDS:
        if name == band:
            return low, high
    raise DifficultyInputError(f"unknown difficulty band: {band}")


def evaluate(spans: Sequence[Span], duration_seconds: float) -> dict[str, object]:
    """Score a material and return everything worth persisting."""
    metrics = compute_metrics(spans, duration_seconds)
    score = score_from_metrics(metrics)
    evidence = {
        "calibration": CALIBRATION_VERSION,
        "metrics": metrics,
        "weights": dict(WEIGHTS),
        "ranges": {
            "wpm": list(WPM_RANGE),
            "mean_sentence_words": list(MEAN_SENTENCE_RANGE),
            "median_gap_seconds": list(GAP_RANGE),
        },
    }
    return {
        "difficulty_score": score,
        "cefr_level": band_for_score(score),
        "difficulty_evidence": evidence,
    }
