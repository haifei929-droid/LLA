"""Material pool: admission gates, ordering and the manual import path.

The pool replaced platform course order, so these tests pin exactly what may and
may not become trainable:

* every gate is a hard gate (accent, license, transcript, ingestion, scoring);
* ordering is continuity -> measured difficulty -> item order, never random;
* an empty band raises ``POOL_EMPTY`` instead of quietly widening the search;
* the manual import path lands in quarantine, is idempotent, and only an
  explicit approval promotes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.difficulty_scoring import (
    DifficultyInputError,
    band_for_score,
    compute_metrics,
    evaluate,
    score_from_metrics,
)
from app.core.local_import import (
    LocalImportService,
    distribute_timings,
    split_sentences,
)
from app.core.material_pool import POOL_EMPTY, MaterialPoolService, PoolSelectionError
from tests.fixtures import DEFAULT_SENTENCES, create_material, make_database, make_settings


# --------------------------------------------------------------------------- #
# difficulty scoring
# --------------------------------------------------------------------------- #
def _spans(words_per_sentence: int, count: int, gap: float) -> list[tuple[str, float, float]]:
    spans: list[tuple[str, float, float]] = []
    cursor = 0.0
    for _ in range(count):
        text = " ".join(["word"] * words_per_sentence)
        spans.append((text, cursor, cursor + 2.0))
        cursor += 2.0 + gap
    return spans


def test_score_is_monotone_and_band_boundaries_are_exact() -> None:
    slow = evaluate(_spans(4, 12, 0.9), duration_seconds=36.0)
    fast = evaluate(_spans(12, 12, 0.2), duration_seconds=36.0)

    assert slow["difficulty_score"] < fast["difficulty_score"]
    assert slow["cefr_level"] in {"A1", "A2"}
    assert fast["cefr_level"] in {"A2", "B1", "B2"}

    assert band_for_score(0.0) == "A1"
    assert band_for_score(32.99) == "A1"
    assert band_for_score(33.0) == "A2"
    assert band_for_score(57.99) == "A2"
    assert band_for_score(58.0) == "B1"
    assert band_for_score(79.99) == "B1"
    assert band_for_score(80.0) == "B2"
    assert band_for_score(100.0) == "B2"


def test_metrics_reject_unusable_input() -> None:
    with pytest.raises(DifficultyInputError):
        compute_metrics(_spans(4, 3, 0.2), duration_seconds=0.0)
    with pytest.raises(DifficultyInputError):
        compute_metrics([], duration_seconds=10.0)
    metrics = compute_metrics(_spans(5, 4, 0.3), duration_seconds=30.0)
    assert metrics["sentence_count"] == 4
    assert metrics["word_count"] == 20
    assert 0.0 <= score_from_metrics(metrics) <= 100.0


# --------------------------------------------------------------------------- #
# pool gates
# --------------------------------------------------------------------------- #
def _governed(
    database,
    material_id: str,
    *,
    score: float = 20.0,
    accent: str | None = "en-GB",
    license_status: str = "CLEARED",
    transcript_verified: int = 1,
    ingestion_status: str = "INGESTED",
    source_platform: str = "50LANGUAGES",
    source_course: str = "book2-en-uk",
    item_order: int = 1,
) -> None:
    create_material(database, material_id)
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE materials
               SET difficulty_score = ?, cefr_level = ?, accent = ?, license_status = ?,
                   transcript_verified = ?, ingestion_status = ?,
                   source_platform = ?, source_course = ?, item_order = ?
             WHERE material_id = ?
            """,
            (
                score,
                band_for_score(score),
                accent,
                license_status,
                transcript_verified,
                ingestion_status,
                source_platform,
                source_course,
                item_order,
                material_id,
            ),
        )


def test_unscored_material_is_not_selectable(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    _governed(database, "scored")
    create_material(database, "unscored")  # no difficulty score at all
    # Even a fully governed row without a measured score stays unselectable.
    with database.connect() as connection:
        connection.execute(
            "UPDATE materials SET ingestion_status = 'INGESTED', accent = 'en-GB',"
            " license_status = 'CLEARED', transcript_verified = 1"
            " WHERE material_id = 'unscored'"
        )

    pool = MaterialPoolService(database)
    chosen = pool.next_material()
    assert chosen["material_id"] == "scored"
    assert pool.status()["excluded"]["unscored"] == 1


def test_non_british_accent_never_enters_the_pool(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    _governed(database, "us", accent="en-US")
    _governed(database, "au", accent="en-AU")
    _governed(database, "unknown", accent=None)
    _governed(database, "uk")

    pool = MaterialPoolService(database)
    assert [row["material_id"] for row in pool._selectable("A1")] == ["uk"]
    status = pool.status()
    assert status["excluded"]["accent_not_uk"] == 3


def test_uncleared_license_or_unverified_transcript_is_excluded(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    _governed(database, "no-license", license_status="UNKNOWN")
    _governed(database, "unverified", transcript_verified=0)
    _governed(database, "quarantined", ingestion_status="QUARANTINED")
    _governed(database, "ok")

    pool = MaterialPoolService(database)
    status = pool.status()
    assert status["excluded"]["license_not_cleared"] == 1
    assert status["excluded"]["transcript_unverified"] == 1
    assert status["excluded"]["not_ingested"] == 1
    assert [row["material_id"] for row in pool._selectable("A1")] == ["ok"]


def test_pool_stays_inside_the_confirmed_band(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    _governed(database, "a1", score=20.0)
    _governed(database, "a2", score=45.0)

    pool = MaterialPoolService(database)
    assert pool.current_band() == "A1"
    chosen = pool.next_material()
    assert chosen["material_id"] == "a1"
    assert chosen["band"] == "A1"

    # The band comes from the confirmed progression row, not from the caller.
    with database.connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO training_difficulty_profiles(scope_id, current_stage, updated_at)"
            " VALUES ('default', 'STAGE_2', datetime('now'))"
        )
    assert pool.current_band() == "A2"
    assert pool.next_material()["material_id"] == "a2"


def test_pool_prefers_same_source_and_course_continuity(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    _governed(database, "same", score=30.0, source_platform="50LANGUAGES", source_course="book2-en-uk")
    _governed(database, "other", score=10.0, source_platform="OTHER", source_course="elsewhere")

    pool = MaterialPoolService(database)
    chosen = pool.next_material(preferred_source="50LANGUAGES", preferred_course="book2-en-uk")
    assert chosen["material_id"] == "same", "continuity wins over a lower score"

    # Without a preference the lower score wins.
    assert pool.next_material()["material_id"] == "other"


def test_empty_pool_raises_pool_empty_instead_of_widening(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    _governed(database, "a1", score=20.0)

    pool = MaterialPoolService(database)
    with pytest.raises(PoolSelectionError) as excinfo:
        pool.next_material(band="B1")
    assert excinfo.value.code == POOL_EMPTY
    assert "B1" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# manual import
# --------------------------------------------------------------------------- #
def test_local_import_is_quarantined_idempotent_and_approvable(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    batch = tmp_path / "inbox" / "enen"
    batch.mkdir(parents=True)
    (batch / "batch.json").write_text(
        '{"source_platform": "50LANGUAGES", "source_course": "book2-en-uk",'
        ' "license_status": "BY-NC-ND-3.0-US", "accent_claim": "en-GB"}',
        encoding="utf-8",
    )
    audio = batch / "ENEN001.mp3"
    audio.write_bytes(b"\x00" * 16)
    (batch / "ENEN001.txt").write_text(" ".join(DEFAULT_SENTENCES), encoding="utf-8")

    service = LocalImportService(database, settings, probe=lambda _path: 90.0)

    first = service.import_batch(batch)
    assert len(first["imported"]) == 1
    assert first["imported"][0]["sentences"] == len(DEFAULT_SENTENCES)
    material_id = first["imported"][0]["material_id"]
    assert material_id == "50LANGUAGES:book2-en-uk:ENEN001"

    with database.connect() as connection:
        row = connection.execute(
            "SELECT ingestion_status, accent, transcript_verified, license_status,"
            "       difficulty_score, difficulty_evidence"
            "  FROM materials WHERE material_id = ?",
            (material_id,),
        ).fetchone()
    assert row["ingestion_status"] == "QUARANTINED"
    assert row["accent"] is None, "import must never guess the accent"
    assert row["transcript_verified"] == 0, "proportional timing is not a verified alignment"
    assert row["license_status"] == "BY-NC-ND-3.0-US"
    assert row["difficulty_score"] is not None
    assert "proportional-word-count" in row["difficulty_evidence"]

    second = service.import_batch(batch)
    assert second["imported"] == []
    assert second["skipped"][0]["reason"] == "DUPLICATE_IDENTITY"

    pool = MaterialPoolService(database)
    with pytest.raises(PoolSelectionError):
        pool.next_material()

    approved = service.approve([material_id], accent="en-GB", license_status="CLEARED")
    assert approved["approved"] == [material_id]

    chosen = pool.next_material()
    assert chosen["material_id"] == material_id
    assert chosen["source_platform"] == "50LANGUAGES"


def test_approval_refuses_unscored_material(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    create_material(database, "raw")

    service = LocalImportService(database, settings, probe=lambda _path: 10.0)
    result = service.approve(["raw", "missing"])
    assert result["approved"] == []
    assert {item["reason"] for item in result["rejected"]} == {"UNSCORED", "MISSING"}


def test_sentence_helpers_keep_every_sentence(tmp_path: Path) -> None:
    sentences = split_sentences("One. Two! Three?")
    assert sentences == ["One.", "Two!", "Three?"]
    spans = distribute_timings(sentences, 30.0)
    assert spans[0].start_time == 0.0
    assert spans[-1].end_time == pytest.approx(30.0, abs=0.01)
