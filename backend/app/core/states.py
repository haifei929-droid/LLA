from __future__ import annotations

from enum import StrEnum


class MaterialState(StrEnum):
    MATERIAL_CREATED = "MATERIAL_CREATED"
    READY_FIRST_LISTEN = "READY_FIRST_LISTEN"
    FIRST_FULL_LISTEN = "FIRST_FULL_LISTEN"
    FIRST_COMPREHENSION_CHECK = "FIRST_COMPREHENSION_CHECK"
    DICTATION_PART_1 = "DICTATION_PART_1"
    DICTATION_PART_2 = "DICTATION_PART_2"
    DICTATION_PART_3 = "DICTATION_PART_3"
    SECOND_FULL_LISTEN = "SECOND_FULL_LISTEN"
    SECOND_COMPREHENSION_CHECK = "SECOND_COMPREHENSION_CHECK"
    READING_AVAILABLE = "READING_AVAILABLE"
    FULL_READING_ASSESSMENT = "FULL_READING_ASSESSMENT"
    LISTENING_COMPLETED = "LISTENING_COMPLETED"
    FULLY_COMPLETED = "FULLY_COMPLETED"


class WeeklyState(StrEnum):
    WEEKLY_ASSESSMENT_READY = "WEEKLY_ASSESSMENT_READY"
    DICTATION_WEEKLY_TEST = "DICTATION_WEEKLY_TEST"
    READING_WEEKLY_TEST = "READING_WEEKLY_TEST"
    WEEKLY_GATE_PASS = "WEEKLY_GATE_PASS"
    REINFORCEMENT_REQUIRED = "REINFORCEMENT_REQUIRED"
    REINFORCEMENT = "REINFORCEMENT"
    TARGETED_RETEST = "TARGETED_RETEST"


class TransitionError(ValueError):
    pass


def next_material_state(current: MaterialState, event: str) -> MaterialState:
    transitions: dict[tuple[MaterialState, str], MaterialState] = {
        (MaterialState.MATERIAL_CREATED, "material_prepared"): MaterialState.READY_FIRST_LISTEN,
        (MaterialState.READY_FIRST_LISTEN, "first_full_listen_completed"): MaterialState.FIRST_COMPREHENSION_CHECK,
        (MaterialState.FIRST_COMPREHENSION_CHECK, "comprehension_submitted"): MaterialState.DICTATION_PART_1,
        (MaterialState.DICTATION_PART_1, "dictation_part_1_completed"): MaterialState.DICTATION_PART_2,
        (MaterialState.DICTATION_PART_2, "dictation_part_2_completed"): MaterialState.DICTATION_PART_3,
        (MaterialState.DICTATION_PART_3, "dictation_part_3_completed"): MaterialState.SECOND_FULL_LISTEN,
        (MaterialState.SECOND_FULL_LISTEN, "second_full_listen_completed"): MaterialState.SECOND_COMPREHENSION_CHECK,
        (MaterialState.SECOND_COMPREHENSION_CHECK, "comprehension_submitted"): MaterialState.READING_AVAILABLE,
        (MaterialState.READING_AVAILABLE, "skip_reading"): MaterialState.LISTENING_COMPLETED,
        (MaterialState.FULL_READING_ASSESSMENT, "full_reading_passed"): MaterialState.FULLY_COMPLETED,
    }
    try:
        return transitions[(current, event)]
    except KeyError as exc:
        raise TransitionError(f"Illegal material transition: {current} + {event}") from exc

