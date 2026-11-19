"""Material pool: admission gates and selection order.

The pool replaces "one platform, one course line, platform order" with a set of
materials whose *admission* is still strictly governed and whose *order* is
measured rather than declared:

    continuity (same source + course as what is being trained)
    -> difficulty score (ascending)
    -> platform item order (ascending)
    -> material id

Hard gates, none of which is a preference:

    accent = 'en-GB'            canonical British only; en-US never enters
    license_status = 'CLEARED'  unlicensed material is never trained
    transcript_verified = 1     an unverified transcript cannot judge dictation
    ingestion_status = 'INGESTED'
    prepare_status = 'READY'
    difficulty_score IS NOT NULL  unscored material is not selectable

Selection stays inside the confirmed difficulty band: the existing eight-week
confirmation mechanism owns the band, this service only refuses to jump ahead.
An empty band raises ``POOL_EMPTY`` instead of sampling the whole library or
quietly relaxing a gate.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.difficulty_scoring import BAND_BOUNDS, band_bounds
from app.db.connection import Database

POOL_EMPTY = "POOL_EMPTY"

#: The eight-week progression still speaks in speed stages; the pool speaks in
#: CEFR bands. This is the only place the two vocabularies meet.
STAGE_TO_BAND: dict[str, str] = {"STAGE_1": "A1", "STAGE_2": "A2", "STAGE_3": "B1"}

_SELECTABLE = """
    SELECT material_id, title, source_platform, source_course, item_order,
           cefr_level, difficulty_score, duration_seconds, speech_rate_wpm
      FROM materials
     WHERE ingestion_status = 'INGESTED'
       AND transcript_verified = 1
       AND accent = 'en-GB'
       AND license_status = 'CLEARED'
       AND (prepare_status IS NULL OR prepare_status = 'READY')
       AND difficulty_score IS NOT NULL
"""

_EXCLUSION_QUERIES: dict[str, str] = {
    "not_ingested": "SELECT COUNT(*) AS n FROM materials WHERE ingestion_status <> 'INGESTED'",
    "accent_not_uk": (
        "SELECT COUNT(*) AS n FROM materials "
        "WHERE ingestion_status = 'INGESTED' AND COALESCE(accent, '') <> 'en-GB'"
    ),
    "license_not_cleared": (
        "SELECT COUNT(*) AS n FROM materials "
        "WHERE ingestion_status = 'INGESTED' AND COALESCE(license_status, 'UNKNOWN') <> 'CLEARED'"
    ),
    "transcript_unverified": (
        "SELECT COUNT(*) AS n FROM materials "
        "WHERE ingestion_status = 'INGESTED' AND COALESCE(transcript_verified, 0) <> 1"
    ),
    "unscored": (
        "SELECT COUNT(*) AS n FROM materials "
        "WHERE ingestion_status = 'INGESTED' AND difficulty_score IS NULL"
    ),
}


class PoolSelectionError(ValueError):
    """Raised when the pool cannot serve the requested band."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MaterialPoolService:
    """Read-side pool queries; ingestion stays with the governed import path."""

    def __init__(self, database: Database, scope_id: str = "default") -> None:
        self.database = database
        self.scope_id = scope_id

    # -- band -----------------------------------------------------------------
    def current_band(self) -> str:
        """Band implied by the confirmed eight-week stage of *this* scope.

        The profile is per scope: reading an arbitrary row would let one scope's
        confirmed stage decide another scope's pool, which is exactly the kind of
        cross-scope leak the governance rules forbid.
        """
        try:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT current_stage FROM training_difficulty_profiles WHERE scope_id = ? LIMIT 1",
                    (self.scope_id,),
                ).fetchone()
        except sqlite3.Error:
            return "A1"
        stage = str(row["current_stage"]) if row is not None and row["current_stage"] else "STAGE_1"
        return STAGE_TO_BAND.get(stage, "A1")

    # -- queries --------------------------------------------------------------
    def _selectable(self, band: str) -> list[dict[str, Any]]:
        low, high = band_bounds(band)
        with self.database.connect() as connection:
            rows = connection.execute(
                _SELECTABLE + " AND difficulty_score >= ? AND difficulty_score < ?",
                (low, high),
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self, band: str | None = None) -> dict[str, Any]:
        """Counts per gate so an empty pool can explain itself."""
        target = band or self.current_band()
        # Read the selectable count before opening the counting connection: a
        # nested connection would start a second write transaction and lock.
        selectable = len(self._selectable(target))
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) AS n FROM materials").fetchone()["n"]
            by_band: dict[str, int] = {}
            try:
                rows = connection.execute(
                    "SELECT COALESCE(cefr_level, 'UNKNOWN') AS band, COUNT(*) AS n "
                    "FROM materials WHERE ingestion_status = 'INGESTED' GROUP BY band"
                ).fetchall()
                by_band = {str(row["band"]): int(row["n"]) for row in rows}
            except sqlite3.Error:
                by_band = {}
            excluded = {}
            for name, sql in _EXCLUSION_QUERIES.items():
                excluded[name] = int(connection.execute(sql).fetchone()["n"])
        return {
            "band": target,
            "bands": [name for name, _low, _high in BAND_BOUNDS],
            "total_materials": int(total),
            "selectable_in_band": selectable,
            "ingested_by_band": by_band,
            "excluded": excluded,
        }

    def next_material(
        self,
        band: str | None = None,
        preferred_source: str | None = None,
        preferred_course: str | None = None,
    ) -> dict[str, Any]:
        """The next material to train, or ``POOL_EMPTY``.

        Raises rather than falling back: a pool that cannot serve the confirmed
        band is a state the caller has to surface (import more material or
        finish the eight-week confirmation), never something to paper over with
        a random pick or a relaxed gate.
        """
        target = band or self.current_band()
        rows = self._selectable(target)
        if not rows:
            raise PoolSelectionError(
                POOL_EMPTY,
                f"no selectable material in the confirmed band {target}; "
                "import verified en-GB material or finish the eight-week confirmation",
            )

        def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
            continuity = 0
            if preferred_source and row.get("source_platform") == preferred_source:
                if not preferred_course or row.get("source_course") == preferred_course:
                    continuity = 0
                else:
                    continuity = 1
            else:
                continuity = 2 if preferred_source else 0
            order = row.get("item_order")
            return (
                continuity,
                float(row.get("difficulty_score") or 0.0),
                int(order) if order is not None else 10**9,
                str(row["material_id"]),
            )

        rows.sort(key=sort_key)
        chosen = dict(rows[0])
        chosen["band"] = target
        chosen["candidates_in_band"] = len(rows)
        return chosen
