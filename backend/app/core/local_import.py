"""Local import: the only door material can enter the pool through.

No network, no crawler, ever. The user downloads the material with a browser
(which is what the platforms actually allow), drops it into a batch folder, and
this service turns it into a *quarantined* material row plus a measured
difficulty score. Promotion into the pool is a separate, explicit human step
(:meth:`LocalImportService.approve`), so nothing unverified becomes trainable by
accident.

Batch layout::

    data/import/inbox/<batch>/batch.json      optional metadata (see below)
    data/import/inbox/<batch>/ENEN001.mp3     one audio file per lesson
    data/import/inbox/<batch>/ENEN001.txt     transcript, same stem as the audio

``batch.json`` fields (all optional)::

    {
      "source_platform": "50LANGUAGES",
      "source_course": "book2-en-uk",
      "license_status": "BY-NC-ND-3.0-US",
      "license_reference": "https://www.goethe-verlag.com/agb.php",
      "accent_claim": "en-GB",
      "title_prefix": "book2"
    }

Sentence timing is *distributed proportionally to word count*, which is an
approximation: the imported material keeps ``transcript_verified = 0`` until a
human confirms the alignment, and the evidence records the method used.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.difficulty_scoring import DifficultyInputError, evaluate
from app.core.materials import MaterialStore
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence

AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".flac")
METADATA_NAME = "batch.json"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DEFAULT_METADATA: dict[str, Any] = {
    "source_platform": "LOCAL_IMPORT",
    "source_course": "unsorted",
    "license_status": "UNKNOWN",
    "license_reference": None,
    "accent_claim": None,
    "title_prefix": "",
}


class LocalImportError(ValueError):
    """Raised when a batch cannot be imported as-is."""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(transcript: str) -> list[str]:
    """Split a transcript into sentences without dropping content."""
    pieces = [_clean(chunk) for chunk in _SENTENCE_SPLIT.split(transcript.replace("\r", " "))]
    sentences = [piece for piece in pieces if piece]
    if not sentences:
        raise LocalImportError("transcript produced no sentence")
    return sentences


def distribute_timings(sentences: Sequence[str], duration: float) -> list[TimestampedSentence]:
    """Give every sentence a proportional slice of the audio.

    Proportional word-count timing is honest for a first pass (and is flagged as
    such in the evidence); it is *not* a forced alignment and therefore never
    counts as verified timing.
    """
    if duration <= 0:
        raise LocalImportError("audio duration must be positive")
    weights = [max(1, len(sentence.split())) for sentence in sentences]
    total = sum(weights)
    spans: list[TimestampedSentence] = []
    cursor = 0.0
    for sentence, weight in zip(sentences, weights):
        share = duration * weight / total
        spans.append(TimestampedSentence(sentence, round(cursor, 3), round(cursor + share, 3)))
        cursor += share
    return spans


def probe_duration(audio_path: Path) -> float:
    """Read an audio duration with ffprobe (already required by the app)."""
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return float(completed.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError) as error:
        raise LocalImportError(f"cannot read audio duration for {audio_path.name}: {error}") from error


class LocalImportService:
    """Import a local batch and, separately, approve it into the pool."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        probe: Callable[[Path], float] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.probe = probe or probe_duration
        self.store = MaterialStore(database)

    # -- metadata -------------------------------------------------------------
    def _metadata(self, batch_dir: Path) -> dict[str, Any]:
        metadata = dict(_DEFAULT_METADATA)
        path = batch_dir / METADATA_NAME
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise LocalImportError(f"{METADATA_NAME} is not valid JSON: {error}") from error
            if isinstance(loaded, dict):
                metadata.update(loaded)
        return metadata

    def _pairs(self, batch_dir: Path) -> list[tuple[Path, Path]]:
        pairs: list[tuple[Path, Path]] = []
        for audio in sorted(batch_dir.iterdir()):
            if audio.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            transcript = audio.with_suffix(".txt")
            if not transcript.is_file():
                raise LocalImportError(f"missing transcript for {audio.name} (expected {transcript.name})")
            pairs.append((audio, transcript))
        if not pairs:
            raise LocalImportError(f"no audio file found in {batch_dir}")
        return pairs

    # -- import ---------------------------------------------------------------
    def import_batch(self, batch_dir: Path) -> dict[str, Any]:
        if not batch_dir.is_dir():
            raise LocalImportError(f"batch directory not found: {batch_dir}")
        metadata = self._metadata(batch_dir)
        platform = str(metadata["source_platform"])
        course = str(metadata["source_course"])
        pairs = self._pairs(batch_dir)

        imported: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for audio, transcript_path in pairs:
            stem = audio.stem
            material_id = f"{platform}:{course}:{stem}"
            with self.database.connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM materials WHERE material_id = ?", (material_id,)
                ).fetchone()
            if exists is not None:
                skipped.append({"material_id": material_id, "reason": "DUPLICATE_IDENTITY"})
                continue

            aligned_path = audio.with_suffix(".aligned.json")
            try:
                duration = float(self.probe(audio))
                sentences = split_sentences(transcript_path.read_text(encoding="utf-8"))
                spans = None
                alignment_meta: dict[str, Any] | None = None
                if aligned_path.is_file():
                    alignment_meta = json.loads(aligned_path.read_text(encoding="utf-8"))
                    pending = alignment_meta.get("unaligned") or []
                    if pending:
                        raise LocalImportError(
                            "alignment left %d line(s) unmatched (%s); fix the transcript or re-run "
                            "scripts/align_transcript.py"
                            % (len(pending), ", ".join(str(item.get("line")) for item in pending[:5]))
                        )
                    spans = self._spans_from_alignment(alignment_meta)
                if spans is None:
                    spans = distribute_timings(sentences, duration)
                scored = evaluate([(s.text, s.start_time, s.end_time) for s in spans], duration)
            except (LocalImportError, DifficultyInputError, OSError) as error:
                failed.append({"audio": audio.name, "reason": str(error)})
                continue

            prefix = str(metadata.get("title_prefix") or "").strip()
            title = f"{prefix} {stem}".strip() if prefix else stem
            material = MaterialPreprocessor().process(
                material_id=material_id,
                title=title,
                audio_path=str(audio),
                transcript=" ".join(sentences),
                timestamped_sentences=spans,
            )
            try:
                self.store.create(
                    material,
                    source={
                        "source_type": "LOCAL_IMPORT",
                        "source_name": f"{platform}/{course}",
                        "source_url": metadata.get("license_reference"),
                    },
                    governance={
                        "source_platform": platform,
                        "source_course": course,
                        "item_order": self._item_order(stem),
                        "accent": None,  # never guessed at import time
                        "cefr_level": scored["cefr_level"],
                        "transcript_verified": 0,
                        "license_status": str(metadata["license_status"]).upper(),
                        "ingestion_status": "QUARANTINED",
                    },
                )
            except (sqlite3.IntegrityError, ValueError) as error:
                failed.append({"audio": audio.name, "reason": f"{type(error).__name__}: {error}"})
                continue

            evidence = dict(scored["difficulty_evidence"])
            evidence["timing"] = "forced-word-alignment" if alignment_meta else "proportional-word-count"
            if alignment_meta:
                evidence["alignment"] = {
                    "coverage": alignment_meta.get("coverage"),
                    "lines": len(alignment_meta.get("lines") or []),
                    "repeats": sum(
                        1 for line in (alignment_meta.get("lines") or []) if line.get("repeats", 1) > 1
                    ),
                }
            evidence["accent_claim"] = metadata.get("accent_claim")
            evidence["license_reference"] = metadata.get("license_reference")
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE materials SET difficulty_score = ?, difficulty_evidence = ? WHERE material_id = ?",
                    (scored["difficulty_score"], json.dumps(evidence, ensure_ascii=False), material_id),
                )
            imported.append(
                {
                    "material_id": material_id,
                    "sentences": len(spans),
                    "duration_seconds": round(duration, 2),
                    "difficulty_score": scored["difficulty_score"],
                    "cefr_level": scored["cefr_level"],
                }
            )

        return {
            "batch": batch_dir.name,
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "status": "QUARANTINED until approved",
        }

    @staticmethod
    def _spans_from_alignment(payload: dict[str, Any]) -> list[TimestampedSentence] | None:
        """Spans straight from the alignment; refuse anything incomplete."""
        lines = payload.get("lines") or []
        if not lines:
            return None
        spans: list[TimestampedSentence] = []
        for item in lines:
            text = str(item.get("text") or "").strip()
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                return None
            if not text or start < 0 or end <= start:
                return None
            spans.append(TimestampedSentence(text, start, end))
        return spans

    @staticmethod
    def _item_order(stem: str) -> int | None:
        match = re.search(r"(\d+)$", stem)
        return int(match.group(1)) if match else None

    # -- approval -------------------------------------------------------------
    def approve(
        self,
        material_ids: Sequence[str],
        *,
        accent: str = "en-GB",
        license_status: str = "CLEARED",
        transcript_verified: bool = True,
    ) -> dict[str, Any]:
        """Promote reviewed material into the pool.

        Refuses material without a measured difficulty score: admission to the
        pool is "evidence present", never "someone called it fine".
        """
        approved: list[str] = []
        rejected: list[dict[str, str]] = []
        for material_id in material_ids:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT difficulty_score FROM materials WHERE material_id = ?", (material_id,)
                ).fetchone()
                if row is None:
                    rejected.append({"material_id": material_id, "reason": "MISSING"})
                    continue
                if row["difficulty_score"] is None:
                    rejected.append({"material_id": material_id, "reason": "UNSCORED"})
                    continue
                connection.execute(
                    """
                    UPDATE materials
                       SET accent = ?, license_status = ?, transcript_verified = ?,
                           ingestion_status = 'INGESTED'
                     WHERE material_id = ?
                    """,
                    (
                        accent,
                        license_status.upper(),
                        1 if transcript_verified else 0,
                        material_id,
                    ),
                )
                approved.append(material_id)
        return {
            "approved": approved,
            "rejected": rejected,
            "accent": accent,
            "license_status": license_status.upper(),
        }


def inbox_root(settings: Settings) -> Path:
    """``<project>/data/import/inbox`` — the manual drop folder."""
    return settings.project_root / "data" / "import" / "inbox"
