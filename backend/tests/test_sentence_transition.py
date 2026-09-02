"""P2 Sentence Transition: backend transition authority, atomic Part completion,
operation idempotency, request-identity conflict, and listen_count recovery.

The submit endpoint decides next state / next sentence / next action and, when
the submitted sentence is the last incomplete one of its Part, completes the
Part atomically in the same transaction. `operation_id` is required; a replayed
id with identical request semantics returns the first-success payload, while a
replayed id with different semantics raises a conflict without writing facts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.dictation_service import DictationService
from app.core.states import MaterialState, TransitionError
from app.core.training_events import TrainingEventService
from app.db.connection import Database
from tests.fixtures import DEFAULT_SENTENCES, create_material, make_database


def _op() -> str:
    return f"op-{uuid4().hex}"


def _drive_to_dictation(db: Database, material_id: str = "m1") -> DictationService:
    events = TrainingEventService(db)
    events.complete_first_listen(material_id)
    events.submit_comprehension(
        material_id=material_id, phase="FIRST", self_rating="30\u201350%", summary="x."
    )
    return DictationService(db)


def _sid(material_id: str, index: int) -> str:
    return f"{material_id}-sentence-{index + 1:03d}"


def _attempt_count(db: Database, sentence_id: str) -> int:
    with db.connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM dictation_attempts WHERE sentence_id = ?", (sentence_id,)
        ).fetchone()[0]


def _operation_count(db: Database, operation_id: str) -> int:
    with db.connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM dictation_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()[0]


def test_ordinary_sentence_advances_to_next(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    result = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=_op(),
    )
    assert result["is_exact_match"] is True
    assert result["transition_type"] == "NEXT_SENTENCE"
    assert result["next_action"] == "CONTINUE_DICTATION"
    assert result["next_sentence_id"] == _sid("m1", 1)
    assert result["next_state"] == "DICTATION_PART_1"
    assert result["next_context"]["part_no"] == 1
    assert not result["next_context"]["sentences"][1]["is_exact"]


def test_last_sentence_atomic_part_transition(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    for i in range(2):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", i),
            user_text=DEFAULT_SENTENCES[i], listen_count=1, operation_id=_op(),
        )
    last = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 2),
        user_text=DEFAULT_SENTENCES[2], listen_count=1, operation_id=_op(),
    )
    assert last["transition_type"] == "PART_COMPLETED"
    assert last["next_state"] == "DICTATION_PART_2"
    assert last["next_action"] == "CONTINUE_DICTATION"
    assert last["next_context"]["part_no"] == 2
    assert not last["next_context"]["sentences"][0]["is_exact"]


def test_idempotent_replay_same_operation_id(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    first = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-1",
    )
    second = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-1",
    )
    assert first == second
    assert _attempt_count(db, _sid("m1", 0)) == 1


def test_response_loss_retry_same_operation_id_replays(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    first = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-retry",
    )
    # Client retried with the same operation_id and same payload -> replay.
    replay = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-retry",
    )
    assert replay == first
    assert replay["is_exact_match"] is True
    assert _attempt_count(db, _sid("m1", 0)) == 1


def test_conflict_different_answer(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-x",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 0),
            user_text="totally different answer", listen_count=1, operation_id="op-x",
        )
    assert _attempt_count(db, _sid("m1", 0)) == 1


def test_conflict_different_sentence(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-s",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 1),
            user_text=DEFAULT_SENTENCES[1], listen_count=1, operation_id="op-s",
        )
    assert _attempt_count(db, _sid("m1", 1)) == 0


def test_conflict_different_material(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    create_material(db, "m2")
    dictation = _drive_to_dictation(db, "m1")
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-m",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m2", sentence_id=_sid("m2", 0),
            user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-m",
        )
    assert _attempt_count(db, _sid("m2", 0)) == 0


def test_conflict_leaves_no_state_change(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    first = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-c",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 0),
            user_text="other answer", listen_count=1, operation_id="op-c",
        )
    assert _attempt_count(db, _sid("m1", 0)) == 1
    assert _operation_count(db, "op-c") == 1
    context = dictation.get_context("m1")
    assert context["current_state"] == "DICTATION_PART_1"
    assert context["sentences"][1]["is_exact"] == 0  # 句 2 未被冲突影响


def test_missing_operation_id_rejected(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    with pytest.raises(ValueError, match="operation_id is required"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 0),
            user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="   ",
        )
    assert _attempt_count(db, _sid("m1", 0)) == 0


def test_concurrent_duplicate_requests_single_attempt(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)

    def submit_once(_: int):
        return dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 0),
            user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id="op-concurrent",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit_once, range(4)))
    assert all(result == results[0] for result in results)
    assert _attempt_count(db, _sid("m1", 0)) == 1
    assert _operation_count(db, "op-concurrent") == 1


def test_refresh_recovery_returns_correct_current_and_listen_count(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=3, operation_id=_op(),
    )
    context = dictation.get_context("m1")
    assert context["current_state"] == "DICTATION_PART_1"
    assert context["part_no"] == 1
    current = [s for s in context["sentences"] if not s["is_exact"]]
    assert len(current) == 2  # 句 2、句 3 尚未 exact
    assert current[0]["sentence_id"] == _sid("m1", 1)
    # 句 1 已提交 listen_count=3，服务端事实可恢复；句 2 尚未提交为 0。
    assert context["sentences"][0]["listen_count"] == 3
    assert context["sentences"][1]["listen_count"] == 0


def test_legacy_part_completion_after_atomic_transition(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    events = TrainingEventService(db)
    for i in range(3):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", i),
            user_text=DEFAULT_SENTENCES[i], listen_count=1, operation_id=_op(),
        )
    with pytest.raises(TransitionError):
        events.complete_dictation_part("m1", 1)
    assert dictation.get_context("m1")["current_state"] == "DICTATION_PART_2"


def test_no_duplicate_part_completion_on_replay(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    for i in range(2):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", i),
            user_text=DEFAULT_SENTENCES[i], listen_count=1, operation_id=_op(),
        )
    first = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 2),
        user_text=DEFAULT_SENTENCES[2], listen_count=1, operation_id="op-last",
    )
    replay = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 2),
        user_text=DEFAULT_SENTENCES[2], listen_count=1, operation_id="op-last",
    )
    assert first == replay
    assert replay["next_state"] == "DICTATION_PART_2"
    with db.connect() as connection:
        row = connection.execute(
            "SELECT current_state, dictation_part_status FROM training_progress WHERE material_id = 'm1'"
        ).fetchone()
    assert row["current_state"] == "DICTATION_PART_2"
    import json

    assert json.loads(row["dictation_part_status"])["1"] is True
    assert json.loads(row["dictation_part_status"])["2"] is False


def test_failed_submit_leaves_no_partial_state(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    with pytest.raises(ValueError, match="order"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 1),
            user_text=DEFAULT_SENTENCES[1], listen_count=1, operation_id="op-bad",
        )
    assert _attempt_count(db, _sid("m1", 1)) == 0
    assert _operation_count(db, "op-bad") == 0


def test_part_3_completion_reaches_second_listen(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    last = None
    for i in range(9):
        last = dictation.submit(
            material_id="m1", sentence_id=_sid("m1", i),
            user_text=DEFAULT_SENTENCES[i], listen_count=1, operation_id=_op(),
        )
    assert last["transition_type"] == "PART_COMPLETED"
    assert last["next_state"] == "SECOND_FULL_LISTEN"
    assert last["next_action"] == "SECOND_LISTEN"
    assert last["next_context"] is None
