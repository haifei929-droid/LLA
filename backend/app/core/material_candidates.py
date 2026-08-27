"""P1 material candidate pipeline: search -> filter -> grade -> rank -> batch.

Candidates exist only as MaterialCandidate rows; they never enter the P0
training library until the user selects one and prepare succeeds. All
filtering thresholds live in versioned configuration (audio_quality module +
stage zone map below), never in UI or prepare logic.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.adapters.speech import WhisperASRProvider
from app.adapters.voa_material import VOALearningEnglishProvider
from app.adapters.web_material import align_sentences, split_sentences
from app.config import Settings
from app.core.audio_quality import ANALYZER_VERSION, THRESHOLD_CONFIG_VERSION, AudioQualityAnalyzer
from app.core.transcript_validator import encode_timestamped, validate_transcript
from app.db.connection import Database

#: VOA program zones per difficulty stage (P1: one variable only — speech rate).
#: Stage 1 = slow English programs; Stages 2/3 = regular-rate VOA programs.
STAGE_ZONES: dict[str, list[int]] = {
    "STAGE_1": [4456, 4791, 5254, 5535, 1581],  # Words and Their Stories, Everyday Grammar, ...
    "STAGE_2": [986, 1689, 955, 959, 979, 987],  # regular-rate programs
    "STAGE_3": [986, 1689, 955, 959, 979, 987],
}
#: Speech-rate targets per stage, used for candidate ranking (P1 3.1).
STAGE_WPM_TARGETS: dict[str, float] = {
    "STAGE_1": 120.0,
    "STAGE_2": 150.0,
    "STAGE_3": 170.0,
}
#: Max raw entries scanned per search (5 zones x ~50 latest each). RSS
#: duration pre-filtering is cheap (no downloads), and 15-20 min slow-English
#: items sit deep in the feed, so the scan window must cover the whole fetch.
PROCESS_LIMIT = 250
#: Bounded retry for provider/quality/transcription failures (P1 8): each
#: candidate's download, quality check and transcription may be retried this
#: many times before the entry is rejected.
RETRY_LIMIT = 2
RETRY_BACKOFF_SECONDS = 1.0
#: Candidate validity window (P1 parameter; versioned in config).
CANDIDATE_TTL_DAYS = 7
MIN_COVERAGE = 0.80
MIN_SENTENCES = 10


class MaterialCandidateService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider=None,
        asr=None,
        quality: AudioQualityAnalyzer | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.provider = provider or VOALearningEnglishProvider()
        self.asr = asr or WhisperASRProvider(model_size="base")
        self.quality = quality or AudioQualityAnalyzer()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def search(
        self,
        *,
        scope_id: str,
        speed_stage: str,
        target_duration_min: float = 15.0,
        target_duration_max: float = 20.0,
        max_results: int = 3,
    ) -> dict[str, object]:
        if speed_stage not in STAGE_ZONES:
            raise ValueError(f"unsupported speed_stage: {speed_stage}")
        if not 1 <= max_results <= 3:
            raise ValueError("max_results must be between 1 and 3")
        if target_duration_max <= target_duration_min:
            raise ValueError("target_duration_max must exceed target_duration_min")

        batch_id = str(uuid4())
        now = self.now_fn()
        rejected: dict[str, int] = {}
        candidates: list[dict[str, object]] = []
        work_dir = self.settings.processed_dir / "p1-candidates" / batch_id

        entries = self.provider._list_episodes(zone_ids=STAGE_ZONES[speed_stage])
        for article_url, mp3_url, title, rss_duration in entries[:PROCESS_LIMIT]:
            duration = rss_duration
            if duration is None:
                rejected["duration_missing"] = rejected.get("duration_missing", 0) + 1
                continue
            if not (target_duration_min * 60 <= duration <= target_duration_max * 60):
                rejected["duration_out_of_range"] = rejected.get("duration_out_of_range", 0) + 1
                continue

            fingerprint = hashlib.sha256(article_url.encode()).hexdigest()[:32]
            if self._candidate_exists(scope_id, fingerprint):
                rejected["duplicate"] = rejected.get("duplicate", 0) + 1
                continue

            article_id = article_url.rstrip("/").split("/")[-1].replace(".html", "")
            try:
                wav_path = self._retry(lambda: self.provider.download_audio(mp3_url, f"p1-{article_id}", work_dir))
            except Exception:
                rejected["download_failed"] = rejected.get("download_failed", 0) + 1
                continue

            quality = self.quality.analyze(str(wav_path))
            if quality.failure_code:
                # Quality check itself failed (unreadable audio): bounded retry.
                try:
                    quality = self._retry(lambda: self.quality.analyze(str(wav_path)))
                except Exception:
                    rejected["quality_failed"] = rejected.get("quality_failed", 0) + 1
                    continue
            if quality.level == "Poor":
                rejected["quality_poor"] = rejected.get("quality_poor", 0) + 1
                continue
            actual_duration = quality.duration_seconds
            if not (target_duration_min * 60 <= actual_duration <= target_duration_max * 60):
                rejected["duration_out_of_range"] = rejected.get("duration_out_of_range", 0) + 1
                continue

            try:
                segments = self._retry(lambda: self.asr.transcribe(str(wav_path)))
            except Exception:
                rejected["transcribe_failed"] = rejected.get("transcribe_failed", 0) + 1
                continue
            transcript = " ".join(segment.text for segment in segments)
            sentences = split_sentences(transcript)
            aligned = align_sentences(sentences, segments)
            validation = validate_transcript(
                transcript, aligned, actual_duration, MIN_COVERAGE, MIN_SENTENCES
            )
            if validation.status != "COMPLETE":
                rejected[f"transcript_{validation.status.lower()}"] = (
                    rejected.get(f"transcript_{validation.status.lower()}", 0) + 1
                )
                continue

            report_id = str(uuid4())
            candidate_id = str(uuid4())
            wpm = (len(transcript.split()) / (actual_duration / 60.0)) if actual_duration else 0.0
            self._store_candidate(
                candidate_id=candidate_id,
                scope_id=scope_id,
                provider_item_id=article_id,
                title=title,
                source_url=article_url,
                audio_url=mp3_url,
                transcript=transcript,
                duration_seconds=actual_duration,
                speed_stage=speed_stage,
                quality_level=quality.level,
                report_id=report_id,
                quality_metrics=_quality_metrics(quality),
                transcript_status=validation.status,
                search_batch_id=batch_id,
                fingerprint=fingerprint,
                audio_path=str(wav_path),
                aligned=aligned,
                now=now,
            )
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "provider": "VOA",
                    "provider_item_id": article_id,
                    "title": title,
                    "duration_seconds": round(actual_duration, 1),
                    "speed_stage": speed_stage,
                    "audio_quality": quality.level,
                    "transcript_status": validation.status,
                    "candidate_status": "CANDIDATE",
                    "speech_rate_wpm": round(wpm, 1),
                }
            )
            if len(candidates) >= max_results:
                break

        candidates = self._rank(candidates, target_duration_min, target_duration_max, speed_stage)
        self._write_search_audit(batch_id, scope_id, speed_stage, candidates, rejected, now)
        return {
            "search_batch_id": batch_id,
            "scope_id": scope_id,
            "speed_stage": speed_stage,
            "candidates": candidates,
            "rejection_summary": rejected,
        }

    def _write_search_audit(
        self,
        batch_id: str,
        scope_id: str,
        speed_stage: str,
        candidates: list[dict[str, object]],
        rejected: dict[str, int],
        now: datetime,
    ) -> None:
        """Persist the search evidence (P1 9): batch, versions, outcome and
        rejection reasons, so filtering decisions are auditable later."""
        import json

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO search_audits(
                    batch_id, scope_id, speed_stage, provider, analyzer_version,
                    threshold_config_version, candidate_count, rejection_summary_json, created_at
                ) VALUES (?, ?, ?, 'VOA', ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, scope_id, speed_stage,
                    ANALYZER_VERSION, THRESHOLD_CONFIG_VERSION,
                    len(candidates), json.dumps(rejected, sort_keys=True), now.isoformat(),
                ),
            )

    def _retry(self, operation: Callable[[], object]) -> object:
        """Bounded retry with linear backoff; raises after RETRY_LIMIT tries."""
        import time as _time

        last_error: Exception | None = None
        for attempt in range(RETRY_LIMIT + 1):
            try:
                return operation()
            except Exception as exc:  # provider/quality/transcription failures
                last_error = exc
                if attempt < RETRY_LIMIT:
                    _time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def _rank(self, candidates: list[dict[str, object]], duration_min: float, duration_max: float, speed_stage: str) -> list[dict[str, object]]:
        # Ordering per P1 3.1: quality, transcript completeness, duration
        # closeness to the band middle, then speech-rate closeness to the
        # stage target. Clear pool first; Acceptable tops up only when the
        # Clear pool is below three.
        mid = (duration_min + duration_max) / 2.0
        stage_target_wpm = STAGE_WPM_TARGETS.get(speed_stage, 120.0)
        ordered = sorted(
            candidates,
            key=lambda c: (
                0 if c["audio_quality"] == "Clear" else 1,
                0 if c.get("transcript_status") == "COMPLETE" else 1,
                abs(float(c["duration_seconds"]) - mid * 60),
                abs(float(c.get("speech_rate_wpm") or 0) - stage_target_wpm),
            ),
        )
        clears = [c for c in ordered if c["audio_quality"] == "Clear"]
        acceptable = [c for c in ordered if c["audio_quality"] == "Acceptable"]
        return (clears + acceptable)[:3]

    def _store_candidate(self, **kwargs) -> None:
        aligned = kwargs.pop("aligned")
        now = kwargs.pop("now")
        expires_at = now + timedelta(days=CANDIDATE_TTL_DAYS)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO material_candidates(
                    candidate_id, scope_id, provider, provider_item_id, title, source_url,
                    audio_url, transcript, duration_seconds, speed_stage, audio_quality,
                    audio_quality_report_id, transcript_status, candidate_status,
                    search_batch_id, content_fingerprint, audio_path,
                    timestamped_sentences_json, created_at, expires_at
                ) VALUES (?, ?, 'VOA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CANDIDATE', ?, ?, ?, ?, ?, ?)
                """,
                (
                    kwargs["candidate_id"], kwargs["scope_id"], kwargs["provider_item_id"],
                    kwargs["title"], kwargs["source_url"], kwargs["audio_url"],
                    kwargs["transcript"], kwargs["duration_seconds"], kwargs["speed_stage"],
                    kwargs["quality_level"], kwargs["report_id"], kwargs["transcript_status"],
                    kwargs["search_batch_id"], kwargs["fingerprint"], kwargs["audio_path"],
                    encode_timestamped(aligned), now.isoformat(), expires_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO audio_quality_reports(
                    report_id, candidate_id, quality_level, metrics_json,
                    threshold_config_version, analyzer_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kwargs["report_id"], kwargs["candidate_id"], kwargs["quality_level"],
                    kwargs["quality_metrics"], THRESHOLD_CONFIG_VERSION, ANALYZER_VERSION,
                    now.isoformat(),
                ),
            )

    def _candidate_exists(self, scope_id: str, fingerprint: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM material_candidates WHERE scope_id = ? AND content_fingerprint = ?",
                (scope_id, fingerprint),
            ).fetchone()
        return row is not None


def _quality_metrics(quality) -> str:
    import json

    return json.dumps(
        {
            "snr_db": round(quality.snr_db, 2),
            "silence_ratio": round(quality.silence_ratio, 4),
            "rms_mean": round(quality.rms_mean, 4),
            "clipping_ratio": round(quality.clipping_ratio, 6),
            "sample_rate": quality.sample_rate,
            "duration_seconds": round(quality.duration_seconds, 2),
            "fingerprint": quality.fingerprint,
        },
        sort_keys=True,
    )
