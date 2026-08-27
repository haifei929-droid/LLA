from __future__ import annotations

import json
from dataclasses import asdict
from uuid import uuid4

from app.core.dictation import DictationErrorType, evaluate_dictation, normalize_for_match
from app.core.states import TransitionError
from app.db.connection import Database


class DictationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_context(self, material_id: str) -> dict[str, object]:
        """Sentence metadata for the dictation UI of the unlocked Part.

        Deliberately excludes sentence text so the blind-listening rule is
        enforced at the API boundary: the dictation screen receives positions
        and timestamps only, never the transcript.
        """
        with self.database.connect() as connection:
            progress = connection.execute(
                "SELECT * FROM training_progress WHERE material_id = ?", (material_id,)
            ).fetchone()
            if progress is None:
                raise KeyError(f"No progress exists for material {material_id}")
            current_state = progress["current_state"]
            if current_state not in {"DICTATION_PART_1", "DICTATION_PART_2", "DICTATION_PART_3"}:
                raise TransitionError("Dictation context is only available while a dictation Part is unlocked")
            part_no = int(current_state.rsplit("_", 1)[1])
            rows = connection.execute(
                """
                SELECT s.sentence_id, s.part_no, s.sequence_no, s.start_time, s.end_time,
                       EXISTS(
                           SELECT 1 FROM dictation_attempts a
                            WHERE a.sentence_id = s.sentence_id AND a.is_exact_match = 1
                       ) AS is_exact
                  FROM sentences s
                 WHERE s.material_id = ? AND s.part_no = ?
                 ORDER BY s.sequence_no
                """,
                (material_id, part_no),
            ).fetchall()
        return {
            "material_id": material_id,
            "current_state": current_state,
            "part_no": part_no,
            "current_sentence_id": progress["current_sentence_id"],
            "current_attempt": progress["current_attempt"],
            "sentences": [dict(row) for row in rows],
        }

    def submit(
        self,
        *,
        material_id: str,
        sentence_id: str,
        user_text: str,
        listen_count: int,
        hint_level: int = 0,
        revealed: bool = False,
        memory_targets: list[str] | None = None,
    ) -> dict[str, object]:
        if listen_count < 1:
            raise ValueError("listen_count must be at least 1")
        if hint_level not in (0, 1, 2):
            raise ValueError("hint_level must be between 0 and 2")
        memory_targets = [normalize_for_match(target) for target in (memory_targets or []) if target.strip()]
        with self.database.connect() as connection:
            sentence = connection.execute(
                "SELECT text FROM sentences WHERE material_id = ? AND sentence_id = ?",
                (material_id, sentence_id),
            ).fetchone()
            if sentence is None:
                raise KeyError("Sentence does not belong to material")
            progress = connection.execute(
                "SELECT * FROM training_progress WHERE material_id = ?", (material_id,)
            ).fetchone()
            if progress is None:
                raise KeyError("No training progress exists for material")
            current_state = progress["current_state"]
            if current_state not in {"DICTATION_PART_1", "DICTATION_PART_2", "DICTATION_PART_3"}:
                raise ValueError("Dictation is not available in the current training state")
            current_part = int(current_state.rsplit("_", 1)[1])
            sentence_row = connection.execute(
                "SELECT part_no, sequence_no FROM sentences WHERE sentence_id = ? AND material_id = ?",
                (sentence_id, material_id),
            ).fetchone()
            if sentence_row["part_no"] != current_part:
                raise ValueError(f"Dictation Part {sentence_row['part_no']} is not currently unlocked")
            expected_row = connection.execute(
                """
                SELECT s.sentence_id
                  FROM sentences s
                 WHERE s.material_id = ? AND s.part_no = ?
                   AND NOT EXISTS (
                       SELECT 1 FROM dictation_attempts a
                        WHERE a.sentence_id = s.sentence_id AND a.is_exact_match = 1
                   )
                 ORDER BY s.sequence_no
                 LIMIT 1
                """,
                (material_id, current_part),
            ).fetchone()
            if expected_row is None:
                raise ValueError("Current Part is complete; submit the Part completion event")
            if expected_row["sentence_id"] != sentence_id:
                raise ValueError("Sentences must be completed in order within the current Part")
            previous = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) AS latest FROM dictation_attempts WHERE sentence_id = ?",
                (sentence_id,),
            ).fetchone()["latest"]
            previous_listen_count = connection.execute(
                "SELECT COALESCE(MAX(listen_count), 0) AS latest FROM dictation_attempts WHERE sentence_id = ?",
                (sentence_id,),
            ).fetchone()["latest"]
            if listen_count < previous_listen_count:
                raise ValueError("listen_count cannot decrease for a sentence")
            prior_memory_targets = self._prior_memory_targets(connection, sentence_id)
            attempt_number = int(previous) + 1
            result = evaluate_dictation(sentence["text"], user_text)
            connection.execute(
                """
                INSERT INTO dictation_attempts(
                    attempt_id, sentence_id, attempt_number, user_text, is_exact_match,
                    listen_count, hint_level, revealed, error_details, memory_targets, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    str(uuid4()),
                    sentence_id,
                    attempt_number,
                    user_text,
                    int(result.is_exact_match),
                    listen_count,
                    hint_level,
                    int(revealed),
                    json.dumps([asdict(error) for error in result.errors], ensure_ascii=False),
                    json.dumps(memory_targets, ensure_ascii=False),
                ),
            )
            self._update_listening_memory(
                connection=connection,
                expected_text=sentence["text"],
                result=result,
                listen_count=listen_count,
                hint_level=hint_level,
                revealed=revealed,
                memory_targets=sorted(set(memory_targets) | prior_memory_targets),
            )
            updated = connection.execute(
                """
                UPDATE training_progress
                   SET current_sentence_id = ?, current_attempt = ?, updated_at = datetime('now'), version = version + 1
                 WHERE material_id = ? AND version = ?
                """,
                (sentence_id, attempt_number, material_id, progress["version"]),
            )
            if updated.rowcount != 1:
                raise TransitionError("Progress changed concurrently; reload before retrying")
        return {
            "sentence_id": sentence_id,
            "attempt_number": attempt_number,
            "listen_count": listen_count,
            "is_exact_match": result.is_exact_match,
            "errors": [asdict(error) for error in result.errors],
            # The transcript is returned only after an explicit Reveal, so the
            # blind-listening boundary stays on the server side.
            "expected_text": sentence["text"] if revealed else None,
        }

    @staticmethod
    def _prior_memory_targets(connection, sentence_id: str) -> set[str]:
        rows = connection.execute(
            "SELECT error_details, memory_targets FROM dictation_attempts WHERE sentence_id = ?",
            (sentence_id,),
        ).fetchall()
        targets: set[str] = set()
        for row in rows:
            for error in json.loads(row["error_details"]):
                if error.get("expected") and error.get("error_type") != DictationErrorType.SPELLING.value:
                    targets.add(normalize_for_match(error["expected"]))
            targets.update(json.loads(row["memory_targets"]))
        return targets

    @staticmethod
    def _update_listening_memory(
        *, connection, expected_text: str, result, listen_count: int, hint_level: int,
        revealed: bool, memory_targets: list[str]
    ) -> None:
        if result.is_exact_match:
            targets = set(memory_targets)
        else:
            targets = {
                error.expected
                for error in result.errors
                if error.expected and error.error_type != DictationErrorType.SPELLING
            }
            targets.update(memory_targets)
        for target in targets:
            row = connection.execute(
                "SELECT * FROM listening_memory WHERE target = ?", (target,)
            ).fetchone()
            if row is None:
                distribution: dict[str, int] = {}
                encounter_count = 0
                hint_count = 0
                reveal_count = 0
                average = None
            else:
                distribution = json.loads(row["first_listen_correct_count"])
                encounter_count = row["encounter_count"]
                hint_count = row["hint_count"]
                reveal_count = row["reveal_count"]
                average = row["avg_attempt_before_correct"]
            encounter_count += 1
            if hint_level:
                hint_count += 1
            if revealed:
                reveal_count += 1
            if result.is_exact_match and not revealed:
                key = str(listen_count)
                distribution[key] = distribution.get(key, 0) + 1
                average = (
                    listen_count
                    if average is None
                    else ((average * (encounter_count - 1)) + listen_count) / encounter_count
                )
            connection.execute(
                """
                INSERT INTO listening_memory(
                    target, encounter_count, first_listen_correct_count,
                    avg_attempt_before_correct, hint_count, reveal_count, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(target) DO UPDATE SET
                    encounter_count = excluded.encounter_count,
                    first_listen_correct_count = excluded.first_listen_correct_count,
                    avg_attempt_before_correct = excluded.avg_attempt_before_correct,
                    hint_count = excluded.hint_count,
                    reveal_count = excluded.reveal_count,
                    last_seen_at = excluded.last_seen_at
                """,
                (target, encounter_count, json.dumps(distribution), average, hint_count, reveal_count),
            )
