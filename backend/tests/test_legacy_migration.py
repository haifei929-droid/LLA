"""Legacy dictation_operations migration compatibility (P2 final blocker).

A database that predates the `normalized_text` column has operations whose
normalized_text was defaulted to ''. The migration backfill must recover the
first submit's request identity from the attempt the operation produced, so a
replayed legacy operation with the same original request replays (not conflicts)
and one with different semantics conflicts (not replays).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.dictation import normalize_for_match
from app.core.dictation_service import DictationService
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


def _set_normalized_text_empty(db: Database, operation_id: str) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE dictation_operations SET normalized_text = '' WHERE operation_id = ?",
            (operation_id,),
        )


def _get_normalized_text(db: Database, operation_id: str) -> str:
    with db.connect() as connection:
        return connection.execute(
            "SELECT normalized_text FROM dictation_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()[0]


def test_legacy_backfill_and_replay(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    op = "legacy-op-1"
    first = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )

    # Simulate a legacy row whose normalized_text was defaulted to ''.
    _set_normalized_text_empty(db, op)
    assert _get_normalized_text(db, op) == ""

    # Re-running initialize triggers the migration backfill.
    db.initialize()
    assert _get_normalized_text(db, op) == normalize_for_match(DEFAULT_SENTENCES[0])

    # Same original request now replays, not conflicts.
    replay = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    assert replay == first
    assert _attempt_count(db, _sid("m1", 0)) == 1


def test_legacy_backfill_then_conflict_different_material(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    create_material(db, "m2")
    dictation = _drive_to_dictation(db, "m1")
    op = "legacy-op-m"
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    _set_normalized_text_empty(db, op)
    db.initialize()
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m2", sentence_id=_sid("m2", 0),
            user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
        )
    assert _attempt_count(db, _sid("m2", 0)) == 0


def test_legacy_backfill_then_conflict_different_sentence(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    op = "legacy-op-s"
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    _set_normalized_text_empty(db, op)
    db.initialize()
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 1),
            user_text=DEFAULT_SENTENCES[1], listen_count=1, operation_id=op,
        )
    assert _attempt_count(db, _sid("m1", 1)) == 0


def test_legacy_backfill_then_conflict_different_answer(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    op = "legacy-op-a"
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    _set_normalized_text_empty(db, op)
    db.initialize()
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 0),
            user_text="totally different answer", listen_count=1, operation_id=op,
        )
    assert _attempt_count(db, _sid("m1", 0)) == 1


def test_migration_backfill_is_idempotent(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    op = "legacy-op-i"
    dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    _set_normalized_text_empty(db, op)
    db.initialize()
    recovered = _get_normalized_text(db, op)
    assert recovered == normalize_for_match(DEFAULT_SENTENCES[0])
    # Repeated migration must not corrupt the backfilled value.
    db.initialize()
    db.initialize()
    assert _get_normalized_text(db, op) == recovered


def test_new_database_behavior_unchanged(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    create_material(db, "m1")
    dictation = _drive_to_dictation(db)
    op = "new-op-1"
    first = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    replay = dictation.submit(
        material_id="m1", sentence_id=_sid("m1", 0),
        user_text=DEFAULT_SENTENCES[0], listen_count=1, operation_id=op,
    )
    assert replay == first
    with pytest.raises(ValueError, match="Idempotency conflict"):
        dictation.submit(
            material_id="m1", sentence_id=_sid("m1", 0),
            user_text="different answer", listen_count=1, operation_id=op,
        )
    assert _attempt_count(db, _sid("m1", 0)) == 1
