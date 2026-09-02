from __future__ import annotations

import json
from dataclasses import asdict
from uuid import uuid4

from app.core.dictation import DictationErrorType, evaluate_dictation, normalize_for_match
from app.core.states import MaterialState, TransitionError, next_material_state
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
                """
                SELECT p.*, m.prepare_status
                  FROM training_progress p
                  JOIN materials m ON m.material_id = p.material_id
                 WHERE p.material_id = ?
                """,
                (material_id,),
            ).fetchone()
            if progress is None:
                raise KeyError(f"No progress exists for material {material_id}")
            if progress["prepare_status"] != "READY":
                raise TransitionError("Material is not ready for training")
            current_state = progress["current_state"]
            if current_state not in {"DICTATION_PART_1", "DICTATION_PART_2", "DICTATION_PART_3"}:
                raise TransitionError("Dictation context is only available while a dictation Part is unlocked")
            part_no = int(current_state.rsplit("_", 1)[1])
            return self._build_context(connection, material_id, current_state, part_no, progress)

    @staticmethod
    def _build_context(
        connection, material_id: str, current_state: str, part_no: int, progress_row
    ) -> dict[str, object]:
        rows = connection.execute(
            """
            SELECT s.sentence_id, s.part_no, s.sequence_no, s.start_time, s.end_time,
                   EXISTS(
                       SELECT 1 FROM dictation_attempts a
                        WHERE a.sentence_id = s.sentence_id AND a.is_exact_match = 1
                   ) AS is_exact,
                   COALESCE((
                       SELECT MAX(a.listen_count) FROM dictation_attempts a
                        WHERE a.sentence_id = s.sentence_id
                   ), 0) AS listen_count
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
            "current_sentence_id": progress_row["current_sentence_id"],
            "current_attempt": progress_row["current_attempt"],
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
        operation_id: str,
    ) -> dict[str, object]:
        if listen_count < 1:
            raise ValueError("listen_count must be at least 1")
        if hint_level not in (0, 1, 2):
            raise ValueError("hint_level must be between 0 and 2")
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id is required")
        memory_targets = [normalize_for_match(target) for target in (memory_targets or []) if target.strip()]
        normalized_text = normalize_for_match(user_text)
        with self.database.connect() as connection:
            cached = connection.execute(
                """
                SELECT material_id, sentence_id, normalized_text, result
                  FROM dictation_operations
                 WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if cached is not None:
                if (
                    cached["material_id"] != material_id
                    or cached["sentence_id"] != sentence_id
                    or cached["normalized_text"] != normalized_text
                ):
                    raise ValueError(
                        "Idempotency conflict: operation_id was already used with different request semantics"
                    )
                return json.loads(cached["result"])
            sentence = connection.execute(
                "SELECT text FROM sentences WHERE material_id = ? AND sentence_id = ?",
                (material_id, sentence_id),
            ).fetchone()
            if sentence is None:
                raise KeyError("Sentence does not belong to material")
            ready = connection.execute(
                "SELECT prepare_status FROM materials WHERE material_id = ?", (material_id,)
            ).fetchone()
            if ready is None or ready["prepare_status"] != "READY":
                raise ValueError("Material is not ready for training")
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
            fresh_progress = connection.execute(
                "SELECT * FROM training_progress WHERE material_id = ?", (material_id,)
            ).fetchone()
            transition = self._resolve_transition(
                connection=connection,
                material_id=material_id,
                current_part=current_part,
                sentence_id=sentence_id,
                is_exact=result.is_exact_match,
                progress=fresh_progress,
            )
            result_payload = {
                "sentence_id": sentence_id,
                "attempt_number": attempt_number,
                "listen_count": listen_count,
                "is_exact_match": result.is_exact_match,
                "errors": [asdict(error) for error in result.errors],
                # The transcript is returned only after an explicit Reveal, so the
                # blind-listening boundary stays on the server side.
                "expected_text": sentence["text"] if revealed else None,
                "completed_sentence": sentence_id if result.is_exact_match else None,
                "transition_type": transition["type"],
                "next_state": transition["next_state"],
                "next_sentence_id": transition["next_sentence_id"],
                "next_action": transition["next_action"],
                "next_context": transition["next_context"],
            }
            # The idempotency ledger row is written in the same transaction as the
            # attempt and Part transition, so a replayed operation_id observes the
            # exact first-success payload without a duplicate attempt or transition.
            if operation_id:
                connection.execute(
                    """
                    INSERT INTO dictation_operations(operation_id, material_id, sentence_id, normalized_text, result, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (operation_id, material_id, sentence_id, normalized_text, json.dumps(result_payload, ensure_ascii=False)),
                )
        return result_payload

    def _resolve_transition(
        self, *, connection, material_id: str, current_part: int, sentence_id: str,
        is_exact: bool, progress,
    ) -> dict[str, object]:
        """Compute the authoritative next step after a submit, and atomically
        complete the Part when the submitted sentence was the last incomplete
        one. Runs inside the submit transaction."""
        if not is_exact:
            return {
                "type": "NONE",
                "next_state": progress["current_state"],
                "next_sentence_id": sentence_id,
                "next_action": "RETRY",
                "next_context": None,
            }
        incomplete = connection.execute(
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
        if incomplete is not None:
            return {
                "type": "NEXT_SENTENCE",
                "next_state": progress["current_state"],
                "next_sentence_id": incomplete["sentence_id"],
                "next_action": "CONTINUE_DICTATION",
                "next_context": self._build_context(
                    connection, material_id, progress["current_state"], current_part, progress
                ),
            }
        # Part completed: perform the Part transition in the same transaction.
        next_state = next_material_state(
            MaterialState(progress["current_state"]), f"dictation_part_{current_part}_completed"
        )
        dictation_status = json.loads(progress["dictation_part_status"])
        dictation_status[str(current_part)] = True
        updated = connection.execute(
            """
            UPDATE training_progress
               SET current_state = ?, dictation_part_status = ?, updated_at = datetime('now'), version = version + 1
             WHERE material_id = ? AND version = ?
            """,
            (next_state.value, json.dumps(dictation_status, sort_keys=True), material_id, progress["version"]),
        )
        if updated.rowcount != 1:
            raise TransitionError("Progress changed concurrently; reload before retrying")
        if next_state in (MaterialState.DICTATION_PART_2, MaterialState.DICTATION_PART_3):
            next_part = int(next_state.value.rsplit("_", 1)[1])
            next_progress = connection.execute(
                "SELECT * FROM training_progress WHERE material_id = ?", (material_id,)
            ).fetchone()
            next_context = self._build_context(
                connection, material_id, next_state.value, next_part, next_progress
            )
            next_sentence = next((s for s in next_context["sentences"] if not s["is_exact"]), None)
            return {
                "type": "PART_COMPLETED",
                "next_state": next_state.value,
                "next_sentence_id": next_sentence["sentence_id"] if next_sentence else None,
                "next_action": "CONTINUE_DICTATION",
                "next_context": next_context,
            }
        return {
            "type": "PART_COMPLETED",
            "next_state": next_state.value,
            "next_sentence_id": None,
            "next_action": "SECOND_LISTEN",
            "next_context": None,
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
