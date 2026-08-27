from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.states import MaterialState, TransitionError, next_material_state
from app.db.connection import Database


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ProgressSnapshot:
    material_id: str
    current_state: MaterialState
    dictation_part_status: dict[str, bool]
    current_sentence_id: str | None
    current_attempt: int
    reading_part_status: dict[str, bool]
    full_reading_status: str
    version: int


class TrainingProgressStore:
    """Persistence boundary for resumable material-level training progress."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure(self, material_id: str) -> ProgressSnapshot:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO training_progress(material_id, current_state, updated_at)
                VALUES (?, ?, ?)
                """,
                (material_id, MaterialState.MATERIAL_CREATED.value, utc_now()),
            )
        return self.get(material_id)

    def get(self, material_id: str) -> ProgressSnapshot:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM training_progress WHERE material_id = ?", (material_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"No progress exists for material {material_id}")
        return ProgressSnapshot(
            material_id=row["material_id"],
            current_state=MaterialState(row["current_state"]),
            dictation_part_status=json.loads(row["dictation_part_status"]),
            current_sentence_id=row["current_sentence_id"],
            current_attempt=row["current_attempt"],
            reading_part_status=json.loads(row["reading_part_status"]),
            full_reading_status=row["full_reading_status"],
            version=row["version"],
        )

    def transition(self, material_id: str, event: str) -> ProgressSnapshot:
        snapshot = self.get(material_id)
        next_state = next_material_state(snapshot.current_state, event)
        dictation_status = snapshot.dictation_part_status.copy()
        if event.startswith("dictation_part_") and event.endswith("_completed"):
            part_no = event.split("_")[2]
            dictation_status[part_no] = True
        reading_status = snapshot.reading_part_status.copy()
        full_reading_status = snapshot.full_reading_status
        if event == "skip_reading":
            full_reading_status = "SKIPPED"

        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE training_progress
                   SET current_state = ?, dictation_part_status = ?, reading_part_status = ?,
                       full_reading_status = ?, updated_at = ?, version = version + 1
                 WHERE material_id = ? AND version = ?
                """,
                (
                    next_state.value,
                    json.dumps(dictation_status, sort_keys=True),
                    json.dumps(reading_status, sort_keys=True),
                    full_reading_status,
                    utc_now(),
                    material_id,
                    snapshot.version,
                ),
            )
            if updated.rowcount != 1:
                raise TransitionError("Progress changed concurrently; reload before retrying")
        return self.get(material_id)

    def complete_reading_part(self, material_id: str, part_no: int) -> ProgressSnapshot:
        if part_no not in (1, 2, 3):
            raise ValueError("part_no must be 1, 2, or 3")
        snapshot = self.get(material_id)
        if snapshot.current_state != MaterialState.READING_AVAILABLE:
            raise TransitionError("Reading parts are only available after the second comprehension check")
        reading_status = snapshot.reading_part_status.copy()
        if reading_status[str(part_no)]:
            raise ValueError(f"Reading Part {part_no} is already complete")
        if any(not reading_status[str(previous)] for previous in range(1, part_no)):
            raise ValueError("Reading parts must be completed in order")
        reading_status[str(part_no)] = True
        all_complete = all(reading_status.values())
        next_state = MaterialState.FULL_READING_ASSESSMENT if all_complete else snapshot.current_state
        full_reading_status = "READY" if all_complete else snapshot.full_reading_status
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE training_progress
                   SET current_state = ?, reading_part_status = ?, full_reading_status = ?,
                       updated_at = ?, version = version + 1
                 WHERE material_id = ? AND version = ?
                """,
                (
                    next_state.value,
                    json.dumps(reading_status, sort_keys=True),
                    full_reading_status,
                    utc_now(),
                    material_id,
                    snapshot.version,
                ),
            )
            if updated.rowcount != 1:
                raise TransitionError("Progress changed concurrently; reload before retrying")
        return self.get(material_id)
