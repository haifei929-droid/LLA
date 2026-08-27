"""P2-2 Listening Memory deepening tests: episodes, first-correct metric
(excluding Hint/Reveal), SPELLING exclusion, weekly-test separation,
threshold configuration, classification, review suggestions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.memory_deepening import DEFAULT_THRESHOLDS, MemoryConfigError, MemoryDeepeningService
from app.db.connection import Database
from tests.fixtures import DEFAULT_SENTENCES, make_database, make_settings

NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)


def _seed_dictation(
    db: Database,
    *,
    sentence_id: str,
    material_id: str,
    expected: str,
    attempts: list[tuple[str, int, int, bool]],  # (user_text, listen_count, hint_level, revealed)
    created_days_ago: int = 0,
) -> None:
    from app.core.dictation import evaluate_dictation

    with db.connect() as connection:
        for index, (user_text, listen_count, hint_level, revealed) in enumerate(attempts, start=1):
            result = evaluate_dictation(expected, user_text)
            connection.execute(
                """
                INSERT INTO dictation_attempts(
                    attempt_id, sentence_id, attempt_number, user_text, is_exact_match,
                    listen_count, hint_level, revealed, error_details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"att-{sentence_id}-{index}", sentence_id, index, user_text,
                    int(result.is_exact_match), listen_count, hint_level, int(revealed),
                    __import__("json").dumps([e.__dict__ for e in result.errors], ensure_ascii=False),
                    (NOW - timedelta(days=created_days_ago)).isoformat(),
                ),
            )


def _material(db: Database, material_id: str = "mem-m1") -> None:
    from app.preprocess.material import MaterialPreprocessor, TimestampedSentence

    timestamped = [
        TimestampedSentence(text, i * 4.0, (i + 1) * 4.0)
        for i, text in enumerate(DEFAULT_SENTENCES[:6])
    ]
    material = MaterialPreprocessor().process(
        material_id=material_id, title="Memory material", audio_path="m.wav",
        transcript=" ".join(DEFAULT_SENTENCES[:6]), timestamped_sentences=timestamped,
    )
    from app.core.materials import MaterialStore

    MaterialStore(db).create(material)


def _service(tmp_path: Path) -> tuple[Database, MemoryDeepeningService]:
    db = make_database(tmp_path)
    _material(db)
    return db, MemoryDeepeningService(db, now_fn=lambda: NOW)


def test_first_correct_listen_uses_listen_count_and_excludes_hint(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    # Sentence: wrong at listen 2 (misheard "lazy" -> "sleepy"), exact at
    # listen 4 without hint -> first=4.
    _seed_dictation(
        db, sentence_id="mem-m1-sentence-001", material_id="mem-m1",
        expected=DEFAULT_SENTENCES[0],
        attempts=[
            (DEFAULT_SENTENCES[0].replace("lazy", "sleepy"), 2, 0, False),
            (DEFAULT_SENTENCES[0], 4, 0, False),
        ],
    )
    # Sentence: hint used before exact -> excluded from first-correct average.
    _seed_dictation(
        db, sentence_id="mem-m1-sentence-002", material_id="mem-m1",
        expected=DEFAULT_SENTENCES[1],
        attempts=[
            (DEFAULT_SENTENCES[1].replace("seashore", "sleepy"), 3, 1, False),
            (DEFAULT_SENTENCES[1], 5, 1, False),
        ],
    )
    # Sentence: reveal only -> excluded from first-correct, but difficulty evidence.
    _seed_dictation(
        db, sentence_id="mem-m1-sentence-003", material_id="mem-m1",
        expected=DEFAULT_SENTENCES[2],
        attempts=[("", 2, 0, True), (DEFAULT_SENTENCES[2], 3, 0, False)],
    )

    service.build_episodes("default")
    result = service.read_memory("default")
    assert result["classification_status"] == "UNCONFIGURED"
    service.save_config("default", **DEFAULT_THRESHOLDS)
    result = service.read_memory("default")

    by_target = {t["target"]: t for t in result["targets"]}
    # "lazyest"->"lazy" word-form error targets "lazy".
    lazy = by_target.get("lazy")
    assert lazy is not None, list(by_target)
    assert lazy["average_first_correct_listen"] == 4.0
    # seashore episode had hint -> excluded from average (None).
    seashore = by_target.get("seashore")
    assert seashore["average_first_correct_listen"] is None
    assert seashore["hint_count"] == 1
    # would: revealed -> excluded from average, reveal counted.
    would = by_target.get("would")
    assert would["average_first_correct_listen"] is None
    assert would["reveal_count"] == 1
    assert would["qualifying_episodes"] >= 1


def test_spelling_errors_never_create_targets(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    _seed_dictation(
        db, sentence_id="mem-m1-sentence-004", material_id="mem-m1",
        expected=DEFAULT_SENTENCES[3],
        attempts=[("The runing dogs do not stop.", 2, 0, False), (DEFAULT_SENTENCES[3], 3, 0, False)],
    )
    service.build_episodes("default")
    service.save_config("default", **DEFAULT_THRESHOLDS)
    result = service.read_memory("default")
    targets = {t["target"] for t in result["targets"]}
    # "runing" is a SPELLING error; "running" is not in the source sentence
    # as a difficulty, so no target may appear from spelling alone.
    assert "running" not in targets


def test_threshold_config_validation_and_versioning(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    with pytest.raises(MemoryConfigError):
        service.save_config("default", short_days=30, long_days=14, min_episodes=3, min_dates=2)
    first = service.save_config("default", short_days=14, long_days=56, min_episodes=3, min_dates=2)
    assert first["config_version"] == "1.0"
    second = service.save_config("default", short_days=7, long_days=56, min_episodes=2, min_dates=2)
    assert second["config_version"] == "1.1"


def test_weekly_test_never_enters_ordinary_memory(tmp_path: Path) -> None:
    """Weekly test attempts live in weekly_test_items, not dictation_attempts,
    so ordinary Memory cannot see them by construction."""
    db, service = _service(tmp_path)
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO weekly_assessments(
                week_id, period_start, period_end, dictation_required, reading_required, gate_status, created_at
            ) VALUES ('W1', '2026-08-01', '2026-08-07', 1, 0, 'WEEKLY_GATE_PASS', '2026-08-07T10:00:00+00:00')
            """
        )
        connection.execute(
            "INSERT INTO weekly_test_items(item_id, week_id, kind, text, is_exact, attempt_count, created_at) VALUES ('t1', 'W1', 'TEST', 'Weekly test sentence with difficulty.', 0, 1, '2026-08-07T10:00:00+00:00')"
        )
    service.build_episodes("default")
    service.save_config("default", **DEFAULT_THRESHOLDS)
    result = service.read_memory("default")
    assert result["targets"] == []


def _add_sentences(db: Database, texts: list[str], material_id: str = "mem-m1") -> None:
    """Insert custom sentences into the material so several sentences share
    the same difficulty target (custom-* ids avoid clashing with the base
    material sentences)."""
    with db.connect() as connection:
        for index, text in enumerate(texts, start=1):
            connection.execute(
                """
                INSERT INTO sentences(sentence_id, material_id, part_no, sequence_no, text, normalized_text, start_time, end_time)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (f"{material_id}-custom-{index:03d}", material_id, 100 + index, text, text.lower(), (index - 1) * 4.0, index * 4.0),
            )


def test_classification_short_vs_long_term(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    _add_sentences(
        db,
        [
            "The lazy fox sleeps all day.",
            "She saw the lazy cat outside.",
            "A lazy dog crossed the road.",
            "We watched the lazy clouds move.",
        ],
    )
    # Recent target: 4 episodes within 14 days, 4 distinct dates.
    for index in range(4):
        _seed_dictation(
            db, sentence_id=f"mem-m1-custom-{index + 1:03d}", material_id="mem-m1",
            expected=["The lazy fox sleeps all day.", "She saw the lazy cat outside.", "A lazy dog crossed the road.", "We watched the lazy clouds move."][index],
            attempts=[
                (["The sleepy fox sleeps all day.", "She saw the sleepy cat outside.", "A sleepy dog crossed the road.", "We watched the sleepy clouds move."][index], 2, 0, False),
                (["The lazy fox sleeps all day.", "She saw the lazy cat outside.", "A lazy dog crossed the road.", "We watched the lazy clouds move."][index], 3, 0, False),
            ],
            created_days_ago=index,
        )
    service.build_episodes("default")
    service.save_config("default", **DEFAULT_THRESHOLDS)
    result = service.read_memory("default")
    classifications = {t["target"]: t["difficulty_classification"] for t in result["targets"]}
    assert classifications.get("lazy") == "SHORT_TERM_DIFFICULT", classifications
    # Thresholds shown beside every classification.
    sample = [t for t in result["targets"] if t["target"] == "lazy"][0]
    assert sample["thresholds_applied"]["short_days"] == 14
    assert sample["thresholds_applied"]["config_version"] == "1.0"


def test_suggestions_default_enabled_frequency_and_controls(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    _add_sentences(
        db,
        [
            "The lazy fox sleeps all day.",
            "She saw the lazy cat outside.",
            "A lazy dog crossed the road.",
            "We watched the lazy clouds move.",
        ],
    )
    expected_texts = [
        "The lazy fox sleeps all day.",
        "She saw the lazy cat outside.",
        "A lazy dog crossed the road.",
        "We watched the lazy clouds move.",
    ]
    wrong_texts = [
        "The sleepy fox sleeps all day.",
        "She saw the sleepy cat outside.",
        "A sleepy dog crossed the road.",
        "We watched the sleepy clouds move.",
    ]
    # Enough evidence to classify as short-term difficult.
    for index in range(4):
        _seed_dictation(
            db, sentence_id=f"mem-m1-custom-{index + 1:03d}", material_id="mem-m1",
            expected=expected_texts[index],
            attempts=[(wrong_texts[index], 2, 0, False), (expected_texts[index], 3, 0, False)],
            created_days_ago=index,
        )
    service.build_episodes("default")
    service.save_config("default", **DEFAULT_THRESHOLDS)

    first = service.generate_suggestions("default")
    assert first["generated"] >= 1
    suggestions = service.list_suggestions("default")
    assert any(s["status"] == "ACTIVE" for s in suggestions)

    # Frequency limit: regenerating within 7 days adds nothing.
    second = service.generate_suggestions("default")
    assert second["generated"] == 0

    # Disable one target -> next generation skips it.
    target = suggestions[0]["target"]
    service.update_preferences("default", action="disable_target", target=target)
    # Batch pause stops generation.
    service.update_preferences("default", action="batch_pause")
    paused = service.generate_suggestions("default")
    assert paused["reason"] == "suggestions paused or disabled"
    service.update_preferences("default", action="batch_pause")
    # Global disable.
    service.update_preferences("default", action="global_disable")
    assert service.generate_suggestions("default")["reason"] == "suggestions paused or disabled"
    service.update_preferences("default", action="restore")
    # Delete suggestion history keeps raw training data intact.
    service.update_preferences("default", action="delete_suggestion_history")
    assert service.list_suggestions("default") == []
    with db.connect() as connection:
        attempts = connection.execute("SELECT COUNT(*) AS n FROM dictation_attempts").fetchone()["n"]
    assert attempts == 8

