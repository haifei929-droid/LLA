"""Material search service: difficulty rules -> provider -> ASR alignment ->
preprocess -> store (Spec 24 provider pipeline)."""

from __future__ import annotations

from pathlib import Path

from app.adapters.speech import WhisperASRProvider
from app.adapters.web_material import BBCLearningEnglishProvider, MaterialSource
from app.config import Settings
from app.core.audio_quality import AudioQualityAnalyzer
from app.core.material_recommender import MaterialRecommender
from app.core.materials import MaterialStore
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence


class MaterialSearchService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        providers=None,
        asr=None,
        recommender: MaterialRecommender | None = None,
        quality: AudioQualityAnalyzer | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        # Provider priority: slow clear sources first (VOA standard), then BBC.
        self.providers = list(providers) if providers else [BBCLearningEnglishProvider()]
        self.asr = asr or WhisperASRProvider()
        self.recommender = recommender or MaterialRecommender()
        self.quality = quality or AudioQualityAnalyzer(
            min_sample_rate=settings.audio_quality_min_sample_rate,
            min_snr_db=settings.audio_quality_min_snr_db,
            max_silence_ratio=settings.audio_quality_max_silence_ratio,
            min_duration_seconds=settings.audio_quality_min_duration_seconds,
        )
        self.store = MaterialStore(database)

    def search_next(self) -> dict[str, object]:
        profile = self._latest_completed_profile()
        gate_passes = self._consecutive_gate_passes()
        criteria = self.recommender.next_criteria(profile, gate_passes)

        excluded = self._imported_source_urls()
        last_error: str | None = None
        for provider in self.providers:
            try:
                source = provider.search_next(
                    exclude_urls=excluded, work_dir=self.settings.processed_dir / "web",
                    asr=self.asr, criteria=criteria,
                )
            except ValueError as exc:
                last_error = str(exc)
                continue
            except (OSError, RuntimeError) as exc:
                # Network/ASR failures degrade to the next provider; the
                # training state is untouched (Spec 31.10).
                last_error = f"source failed: {exc}"
                continue
            try:
                quality = self.quality.analyze(source.audio_path)
                quality_passed = quality.passed
            except (OSError, ValueError) as exc:
                last_error = f"source rejected: {exc}"
                quality_passed = False
            if not quality_passed:
                if last_error is None:
                    last_error = (
                        f"source rejected by audio-quality gate "
                        f"(snr={quality.snr_db:.0f}dB, silence={quality.silence_ratio:.0%})"
                    )
                excluded.add(source.source_url or "")
                continue
            material = MaterialPreprocessor().process(
                material_id=source.material_id,
                title=source.title,
                audio_path=source.audio_path,
                transcript=source.transcript,
                timestamped_sentences=[
                    TimestampedSentence(text=text, start_time=start, end_time=end)
                    for text, start, end in source.timestamped_sentences
                ],
            )
            self._store_with_source(material, source)
            return {
                "material_id": material.material_id,
                "title": material.title,
                "duration_seconds": material.duration_seconds,
                "speech_rate_wpm": material.speech_rate_wpm,
                "sentence_count": len(material.sentences),
                "source_url": source.source_url,
                "source_name": source.source_name,
                "upgrade_available": criteria.upgrade_available,
                "criteria": {
                    "duration_band": criteria.duration_band,
                    "wpm_max": criteria.wpm_max,
                },
            }
        raise ValueError(last_error or "No new material could be searched")

    def skip(self, material_id: str) -> dict[str, object]:
        """User skip: the material is retired from the active list (Spec 12
        pace control). Its source URL stays excluded from future searches."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT material_id FROM materials WHERE material_id = ?", (material_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No material exists for {material_id}")
            connection.execute(
                "UPDATE materials SET status = 'SKIPPED' WHERE material_id = ?", (material_id,)
            )
        return {"material_id": material_id, "status": "SKIPPED"}

    def _store_with_source(self, material, source: MaterialSource) -> None:
        self.store.create(material)
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE materials
                   SET source_url = ?, source_name = ?
                 WHERE material_id = ?
                """,
                (source.source_url, source.source_name, material.material_id),
            )

    def _latest_completed_profile(self) -> dict[str, float] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT m.duration_seconds, m.speech_rate_wpm
                  FROM materials m
                  JOIN training_progress p ON p.material_id = m.material_id
                 WHERE p.current_state IN ('LISTENING_COMPLETED', 'FULLY_COMPLETED')
                 ORDER BY p.updated_at DESC
                 LIMIT 1
                """,
            ).fetchone()
        if row is None or row["speech_rate_wpm"] is None:
            return None
        return {
            "duration_minutes": row["duration_seconds"] / 60.0,
            "wpm": row["speech_rate_wpm"],
        }

    def _consecutive_gate_passes(self) -> int:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT gate_status FROM weekly_assessments ORDER BY created_at DESC"
            ).fetchall()
        passes = 0
        for row in rows:
            if row["gate_status"] == "WEEKLY_GATE_PASS":
                passes += 1
            else:
                break
        return passes

    def _imported_source_urls(self) -> set[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT source_url FROM materials WHERE source_url IS NOT NULL"
            ).fetchall()
        return {row["source_url"] for row in rows}
