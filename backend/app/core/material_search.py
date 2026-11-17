"""Material search service: the legacy `/api/materials/next` entry.

The old entry point used to pick a provider list and apply the two-week
recommendation rule; both are governance bypasses, so the service now delegates
to the governed candidate flow: active source -> next qualified course item of
that platform -> atomic ingestion. There is no parallel source selection and no
cross-platform fallback: a provider failure stays a failure instead of quietly
switching source, and difficulty upgrades stay with the existing eight-week
confirmation mechanism (P1 difficulty progression).
"""

from __future__ import annotations

from app.config import Settings
from app.core.material_candidates import MaterialCandidateService, SourceGovernanceError
from app.core.material_preparation import MaterialPreparationService, MaterialSelectionError
from app.core.materials import MaterialStore
from app.db.connection import Database


class MaterialSearchService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        providers=None,
        asr=None,
        recommender=None,
        quality=None,
        history=None,
        candidates: MaterialCandidateService | None = None,
        preparation: MaterialPreparationService | None = None,
        difficulty=None,
    ) -> None:
        self.database = database
        self.settings = settings
        provider_list = list(providers) if providers else []
        self.providers = provider_list
        self.candidates = candidates or MaterialCandidateService(
            database,
            settings,
            provider=(provider_list[0] if provider_list else None),
            asr=asr,
            quality=quality,
        )
        self.preparation = preparation or MaterialPreparationService(
            database, settings, candidates=self.candidates
        )
        self.store = MaterialStore(database)
        self.history = history
        self.difficulty = difficulty
        # Kept only so nothing still importable disappears; it no longer
        # decides source selection or upgrades.
        self.recommender = recommender

    def search_next(self, scope_id: str = "default") -> dict[str, object]:
        active = self.candidates.active_source(scope_id)
        if active is None:
            raise ValueError(
                "SOURCE_NOT_APPROVED: no active material source; "
                "a platform must be confirmed before fetching the next material"
            )
        candidate = self.candidates.next_qualified(scope_id)
        if candidate is None:
            # One governed discovery pass over the active platform only.
            try:
                self.candidates.search(scope_id=scope_id)
            except SourceGovernanceError:
                raise
            except Exception as exc:
                raise ValueError(f"source failed: {exc}") from exc
            candidate = self.candidates.next_qualified(scope_id)
        if candidate is None:
            raise ValueError(
                "No qualified material is available from the active source; "
                "review the pending candidates or the completion report"
            )

        idempotency_key = (
            f"next:{scope_id}:{active['platform']}:{active['cycle']}:"
            f"{candidate['candidate_id']}"
        )
        try:
            prepared = self.preparation.prepare(
                candidate["candidate_id"], scope_id, idempotency_key
            )
        except MaterialSelectionError as exc:
            raise ValueError(f"{exc.code}: {exc}") from exc

        material = self.store.get(prepared["material_id"]) or {}
        prompt = (
            self.difficulty.current_prompt(scope_id) if self.difficulty is not None else None
        )
        return {
            "material_id": material.get("material_id", prepared["material_id"]),
            "title": material.get("title"),
            "duration_seconds": material.get("duration_seconds"),
            "speech_rate_wpm": material.get("speech_rate_wpm"),
            "sentence_count": len(material.get("sentences") or []),
            "source_url": material.get("source_url"),
            "source_name": material.get("source_name"),
            "source_platform": material.get("source_platform"),
            "accent": material.get("accent"),
            "cefr_level": material.get("cefr_level"),
            "ingestion_status": material.get("ingestion_status"),
            "candidate_id": candidate["candidate_id"],
            "upgrade_available": prompt is not None,
            "upgrade_prompt_id": None if prompt is None else prompt["prompt_id"],
        }

    def skip(self, material_id: str) -> dict[str, object]:
        """User skip: the material is retired from the active list (Spec 12
        pace control). Its source identity stays ingested, so the next
        qualified course item is what the next fetch returns."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT material_id FROM materials WHERE material_id = ?", (material_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No material exists for {material_id}")
            connection.execute(
                "UPDATE materials SET status = 'SKIPPED' WHERE material_id = ?", (material_id,)
            )
        if self.history is not None:
            self.history.record(
                "default", "MATERIAL_SKIPPED",
                source_record_ids=[material_id], actor="USER",
                reason="user skipped material",
            )
        return {"material_id": material_id, "status": "SKIPPED"}
