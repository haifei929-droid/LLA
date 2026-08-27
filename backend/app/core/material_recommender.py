"""Material difficulty recommender (Spec 3.1/16).

Difficulty has exactly two variables: duration and speech rate. Only one
variable may upgrade at a time, upgrades are never triggered by accumulated
hours, and the trigger to ask for an upgrade is weekly-test stability. The
recommender turns the previous material's profile plus the weekly-gate history
into the criteria the material provider searches against.
"""

from __future__ import annotations

from dataclasses import dataclass


def rate_band(wpm: float) -> str:
    if wpm < 120:
        return "slow"
    if wpm <= 165:
        return "medium"
    return "fast"


def duration_band(minutes: float) -> str:
    if minutes < 10:
        return "short"
    if minutes <= 20:
        return "standard"
    return "long"


@dataclass(frozen=True)
class SearchCriteria:
    #: Preferred duration band; None = any.
    duration_band: str | None = None
    #: Maximum acceptable speech rate for the searched material.
    wpm_max: float | None = None
    #: True when the weekly gate has been stable enough to offer an upgrade.
    upgrade_available: bool = False


class MaterialRecommender:
    """Deterministic next-material criteria from progress and gate history."""

    #: Consecutive weekly-gate passes required before suggesting an upgrade.
    stable_pass_rounds: int = 2

    def next_criteria(self, previous_profile: dict[str, float] | None, recent_gate_passes: int) -> SearchCriteria:
        """Criteria for the next search.

        Start: standard duration, slow speech. After `stable_pass_rounds`
        consecutive gate passes, offer exactly one variable upgrade: duration
        first (standard -> long), then rate (slow -> medium -> fast). Each
        round after the first stable window upgrades the other variable, so
        only one variable changes at a time.
        """
        if previous_profile is None:
            return SearchCriteria(duration_band="standard", wpm_max=120.0, upgrade_available=False)

        current_duration = duration_band(previous_profile["duration_minutes"])
        current_rate = rate_band(previous_profile["wpm"])
        upgrade_available = recent_gate_passes >= self.stable_pass_rounds

        target_duration = current_duration
        target_wpm = _band_wpm_max(current_rate)
        if upgrade_available:
            if current_duration == "standard":
                target_duration = "long"
            elif current_rate == "slow":
                target_wpm = _band_wpm_max("medium")
            elif current_rate == "medium":
                target_wpm = _band_wpm_max("fast")
            # long + fast: both maxima already reached; stay put.
        return SearchCriteria(
            duration_band=target_duration,
            wpm_max=target_wpm,
            upgrade_available=upgrade_available,
        )


def _band_wpm_max(band: str) -> float:
    return {"slow": 120.0, "medium": 165.0, "fast": 500.0}[band]
