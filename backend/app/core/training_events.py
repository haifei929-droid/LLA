from __future__ import annotations

from uuid import uuid4

from app.core.progress import ProgressSnapshot, TrainingProgressStore, utc_now
from app.core.states import MaterialState, TransitionError, next_material_state
from app.db.connection import Database


class TrainingEventService:
    """Application boundary for events that advance the material state machine."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.progress_store = TrainingProgressStore(database)

    def complete_first_listen(self, material_id: str) -> ProgressSnapshot:
        return self.progress_store.transition(material_id, "first_full_listen_completed")

    def submit_comprehension(
        self,
        *,
        material_id: str,
        phase: str,
        self_rating: str,
        summary: str,
    ) -> ProgressSnapshot:
        expected_state = {
            "FIRST": MaterialState.FIRST_COMPREHENSION_CHECK,
            "SECOND": MaterialState.SECOND_COMPREHENSION_CHECK,
        }.get(phase)
        if expected_state is None:
            raise ValueError("phase must be FIRST or SECOND")
        if not summary.strip():
            raise ValueError("summary must not be empty")
        next_state = next_material_state(expected_state, "comprehension_submitted")
        with self.database.connect() as connection:
            progress = connection.execute(
                "SELECT version, current_state FROM training_progress WHERE material_id = ?",
                (material_id,),
            ).fetchone()
            if progress is None:
                raise KeyError(f"No progress exists for material {material_id}")
            if progress["current_state"] != expected_state.value:
                raise TransitionError(
                    f"Comprehension phase {phase} is not available in state {progress['current_state']}"
                )
            connection.execute(
                """
                INSERT INTO comprehension_checks(
                    check_id, material_id, phase, self_rating, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), material_id, phase, self_rating, summary.strip(), utc_now()),
            )
            updated = connection.execute(
                """
                UPDATE training_progress
                   SET current_state = ?, updated_at = ?, version = version + 1
                 WHERE material_id = ? AND version = ?
                """,
                (next_state.value, utc_now(), material_id, progress["version"]),
            )
            if updated.rowcount != 1:
                raise TransitionError("Progress changed concurrently; reload before retrying")
        return self.progress_store.get(material_id)

    def complete_dictation_part(self, material_id: str, part_no: int) -> ProgressSnapshot:
        if part_no not in (1, 2, 3):
            raise ValueError("part_no must be 1, 2, or 3")
        expected_state = MaterialState(f"DICTATION_PART_{part_no}")
        with self.database.connect() as connection:
            progress = connection.execute(
                "SELECT current_state FROM training_progress WHERE material_id = ?",
                (material_id,),
            ).fetchone()
            if progress is None:
                raise KeyError(f"No progress exists for material {material_id}")
            if progress["current_state"] != expected_state.value:
                raise TransitionError(
                    f"Dictation Part {part_no} is not available in state {progress['current_state']}"
                )
            incomplete = connection.execute(
                """
                SELECT s.sentence_id
                  FROM sentences s
                 WHERE s.material_id = ? AND s.part_no = ?
                   AND NOT EXISTS (
                       SELECT 1 FROM dictation_attempts a
                        WHERE a.sentence_id = s.sentence_id AND a.is_exact_match = 1
                   )
                 LIMIT 1
                """,
                (material_id, part_no),
            ).fetchone()
            if incomplete is not None:
                raise ValueError("Every sentence in the current Part must be exactly completed first")
        return self.progress_store.transition(material_id, f"dictation_part_{part_no}_completed")

    def complete_second_listen(self, material_id: str) -> ProgressSnapshot:
        return self.progress_store.transition(material_id, "second_full_listen_completed")

    def complete_reading_part(self, material_id: str, part_no: int) -> ProgressSnapshot:
        if not self._has_passing_score(material_id, "PART", part_no):
            raise TransitionError(
                f"Reading Part {part_no} has not passed three-dimension scoring yet"
            )
        return self.progress_store.complete_reading_part(material_id, part_no)

    def _has_passing_score(self, material_id: str, scope: str, part_no: int | None) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM reading_attempts
                 WHERE material_id = ? AND scope = ? AND overall_pass = 1
                   AND (part_no IS ? OR part_no = ?)
                 LIMIT 1
                """,
                (material_id, scope, part_no, part_no),
            ).fetchone()
        return row is not None

    def complete_full_reading_assessment(
        self,
        material_id: str,
        passed: bool,
        *,
        reference_duration: float | None = None,
        user_duration: float | None = None,
        speed_result: str | None = None,
        pause_result: str | None = None,
        stress_result: str | None = None,
    ) -> ProgressSnapshot:
        snapshot = self.progress_store.get(material_id)
        if snapshot.current_state != MaterialState.FULL_READING_ASSESSMENT:
            raise TransitionError(
                "The full reading assessment is only available after all reading parts are complete"
            )
        if passed and not self._has_passing_score(material_id, "FULL", None):
            raise TransitionError("The full reading assessment has not passed scoring yet")
        with self.database.connect() as connection:
            attempt_number = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_attempt FROM reading_attempts WHERE material_id = ? AND scope = 'FULL'",
                (material_id,),
            ).fetchone()["next_attempt"]
            connection.execute(
                """
                INSERT INTO reading_attempts(
                    attempt_id, material_id, scope, attempt_number,
                    reference_duration, user_duration, speed_result, pause_result,
                    stress_result, overall_pass, created_at
                ) VALUES (?, ?, 'FULL', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    material_id,
                    attempt_number,
                    reference_duration,
                    user_duration,
                    speed_result,
                    pause_result,
                    stress_result,
                    int(passed),
                    utc_now(),
                ),
            )
        if not passed:
            return snapshot
        return self.progress_store.transition(material_id, "full_reading_passed")

    def skip_reading(self, material_id: str) -> ProgressSnapshot:
        return self.progress_store.transition(material_id, "skip_reading")


def progress_payload(snapshot: ProgressSnapshot) -> dict[str, object]:
    return {
        "material_id": snapshot.material_id,
        "current_state": snapshot.current_state,
        "dictation_part_status": snapshot.dictation_part_status,
        "current_sentence_id": snapshot.current_sentence_id,
        "current_attempt": snapshot.current_attempt,
        "reading_part_status": snapshot.reading_part_status,
        "full_reading_status": snapshot.full_reading_status,
        "version": snapshot.version,
    }
