"""Reading Rule Engine: independent PASS / CLOSE / FAIL per dimension (Spec 9.3).

Three dimensions are judged separately and never averaged into one score.
Thresholds are parameters (Spec 33: calibrate with real samples; P0 ships
defaults) supplied by Settings so deployment can tune them from env vars.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.audio import AudioFeatures


@dataclass(frozen=True)
class ReadingThresholds:
    """Tolerance defaults; each is overridable through Settings env vars."""

    #: Allowed relative difference between user and reference duration.
    speed_tolerance_pass: float = 0.15
    speed_tolerance_close: float = 0.30
    #: Allowed absolute difference in pause count.
    pause_tolerance_pass: int = 2
    pause_tolerance_close: int = 4
    #: Allowed relative difference in RMS coefficient of variation (stress proxy).
    stress_tolerance_pass: float = 0.25
    stress_tolerance_close: float = 0.45


@dataclass(frozen=True)
class ReadingScore:
    speed: str  # PASS | CLOSE | FAIL
    pause: str
    stress: str
    reference_duration: float
    user_duration: float
    reference_pause_count: int
    user_pause_count: int
    overall_pass: bool


def _level(relative_difference: float, pass_tolerance: float, close_tolerance: float) -> str:
    if relative_difference <= pass_tolerance:
        return "PASS"
    if relative_difference <= close_tolerance:
        return "CLOSE"
    return "FAIL"


class ReadingRuleEngine:
    def __init__(self, thresholds: ReadingThresholds | None = None) -> None:
        self.thresholds = thresholds or ReadingThresholds()

    def score(self, reference: AudioFeatures, user: AudioFeatures) -> ReadingScore:
        speed_difference = abs(user.duration_seconds - reference.duration_seconds) / reference.duration_seconds
        speed = _level(
            speed_difference,
            self.thresholds.speed_tolerance_pass,
            self.thresholds.speed_tolerance_close,
        )

        pause_difference = abs(len(user.pauses) - len(reference.pauses))
        # Absolute tolerances floor, then scale with the reference pause count
        # (calibrated on real voice: a 6-minute text has ~30 breath pauses, so
        # a fixed +-2 is unrealistically strict).
        pause_pass = max(self.thresholds.pause_tolerance_pass, round(len(reference.pauses) * 0.10))
        pause_close = max(self.thresholds.pause_tolerance_close, round(len(reference.pauses) * 0.20))
        pause = (
            "PASS"
            if pause_difference <= pause_pass
            else "CLOSE"
            if pause_difference <= pause_close
            else "FAIL"
        )

        stress_reference = reference.rms_cv
        stress_difference = (
            abs(user.rms_cv - stress_reference) / stress_reference if stress_reference > 0 else 0.0
        )
        stress = _level(
            stress_difference,
            self.thresholds.stress_tolerance_pass,
            self.thresholds.stress_tolerance_close,
        )

        return ReadingScore(
            speed=speed,
            pause=pause,
            stress=stress,
            reference_duration=reference.duration_seconds,
            user_duration=user.duration_seconds,
            reference_pause_count=len(reference.pauses),
            user_pause_count=len(user.pauses),
            overall_pass=speed == "PASS" and pause == "PASS" and stress == "PASS",
        )
