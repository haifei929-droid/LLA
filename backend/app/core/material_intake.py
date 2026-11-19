"""Material intake: the browser-facing closed loop for staged material.

This is the HTTP-facing orchestration on top of the pieces that already exist:
staged batches live in ``data/import/inbox/<batch>/``, ``LocalImportService``
turns an audio file plus transcript into a quarantined material,
``forced_alignment`` measures sentence boundaries, ``MaterialPoolService``
decides what is trainable, and ``acquisition`` fetches the one officially offered
audio pack.

What is new here is *only* the intake state machine around those files:

    audio only -> transcript saved -> aligned -> approved (ingested) -> trainable

Every step is derived from files on disk plus the existing database columns; there
is no parallel status table. Two rules are load-bearing:

* nothing here guesses. No fixed 8-second timeline, no invented sentences, no
  automatic accent, no automatic approval of an ASR draft.
* the approval path re-checks the evidence server-side (accent, licence,
  transcript, durable alignment of *this* audio and *this* transcript), because a
  checkbox in a browser is not evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.acquisition import AcquisitionError, fetch_official_pack, first_automation_allowed, require_database
from app.core.asr_draft import ASRUnavailable, transcribe_words
from app.core.forced_alignment import align_lines
from app.core.local_import import AUDIO_SUFFIXES, LocalImportError, LocalImportService, probe_duration
from app.core.material_candidates import MaterialCandidateService, SourceGovernanceError, active_source_platform
from app.core.material_pool import MaterialPoolService
from app.db.connection import Database

UPLOAD_BATCH = "uploads"
#: Uploads larger than this are refused before anything touches disk.
MAX_UPLOAD_BYTES = 160 * 1024 * 1024
#: Lines whose match is this good are needed for the alignment to count as complete.
MIN_ALIGNMENT_COVERAGE = 0.95
DEFAULT_UPLOAD_METADATA = {
    "source_platform": "LOCAL_UPLOAD",
    "source_course": UPLOAD_BATCH,
    "license_status": "UNKNOWN",
    "license_reference": None,
    "accent_claim": None,
    "title_prefix": "",
}
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")

STATUS_LABELS = {
    "AUDIO_ONLY": "仅音频，待补文字稿",
    "NEEDS_REVIEW": "待校对/待对齐",
    "ALIGNED": "已对齐，待审核",
    "APPROVED": "已入库",
    "TRAINABLE": "当前档位可选",
}


class IntakeError(ValueError):
    """Raised when an intake operation must not proceed."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


@dataclass(frozen=True)
class IntakePaths:
    batch_dir: Path
    audio: Path
    transcript: Path
    alignment: Path
    metadata: Path


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class MaterialIntakeService:
    """Staged material: enumerate, upload, transcribe, align, approve."""

    def __init__(self, database: Database, settings: Settings, scope_id: str = "default") -> None:
        self.database = database
        self.settings = settings
        self.scope_id = scope_id
        self.inbox = Path(settings.project_root) / "data" / "import" / "inbox"
        self.local = LocalImportService(database, settings)
        self.pool = MaterialPoolService(database, scope_id=scope_id)

    # ------------------------------------------------------------------ paths
    def _paths(self, batch: str, stem: str) -> IntakePaths:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", batch) or not re.fullmatch(r"[A-Za-z0-9._-]+", stem):
            raise IntakeError("INVALID_ITEM", "item id contains unsupported characters")
        batch_dir = self.inbox / batch
        audio = next((batch_dir / f"{stem}{suffix}" for suffix in AUDIO_SUFFIXES if (batch_dir / f"{stem}{suffix}").is_file()), None)
        if audio is None:
            raise IntakeError("ITEM_NOT_FOUND", f"no staged audio for {batch}/{stem}")
        return IntakePaths(
            batch_dir=batch_dir,
            audio=audio,
            transcript=batch_dir / f"{stem}.txt",
            alignment=batch_dir / f"{stem}.aligned.json",
            metadata=batch_dir / f"{stem}.meta.json",
        )

    def _batch_metadata(self, batch_dir: Path) -> dict[str, Any]:
        meta = dict(DEFAULT_UPLOAD_METADATA)
        path = batch_dir / "batch.json"
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                meta.update(loaded)
        return meta

    def _item_metadata(self, paths: IntakePaths, batch_meta: dict[str, Any]) -> dict[str, Any]:
        meta = dict(batch_meta)
        if paths.metadata.is_file():
            try:
                loaded = json.loads(paths.metadata.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                meta.update(loaded)
        return meta

    @staticmethod
    def item_id(batch: str, stem: str) -> str:
        return f"{batch}:{stem}"

    def _split_item(self, item_id: str) -> tuple[str, str]:
        if ":" not in item_id:
            raise IntakeError("INVALID_ITEM", "item id must look like '<batch>:<stem>'")
        batch, stem = item_id.split(":", 1)
        return batch, stem

    # ----------------------------------------------------------------- status
    def _material_row(self, material_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT material_id, title, ingestion_status, accent, license_status, transcript_verified,"
                "       difficulty_score, cefr_level, source_platform, source_course, item_order, duration_seconds"
                "  FROM materials WHERE material_id = ?",
                (material_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def describe(self, batch: str, stem: str) -> dict[str, Any]:
        paths = self._paths(batch, stem)
        batch_meta = self._batch_metadata(paths.batch_dir)
        meta = self._item_metadata(paths, batch_meta)
        platform = str(meta.get("source_platform") or DEFAULT_UPLOAD_METADATA["source_platform"])
        course = str(meta.get("source_course") or DEFAULT_UPLOAD_METADATA["source_course"])
        material_id = self.local.material_id_for(platform, course, stem)
        row = self._material_row(material_id)

        has_transcript = paths.transcript.is_file() and bool(paths.transcript.read_text(encoding="utf-8").strip())
        alignment = self._alignment_state(paths)
        blockers: list[str] = []

        if row is None:
            status = "AUDIO_ONLY" if not has_transcript else ("ALIGNED" if alignment["usable"] else "NEEDS_REVIEW")
        elif row["ingestion_status"] == "INGESTED":
            status = "APPROVED"
        else:
            status = "NEEDS_REVIEW"

        if not has_transcript:
            blockers.append("TRANSCRIPT_MISSING")
        if not alignment["present"]:
            blockers.append("ALIGNMENT_MISSING")
        elif alignment["stale"]:
            blockers.append("ALIGNMENT_STALE")
        elif alignment["unaligned"]:
            blockers.append("ALIGNMENT_INCOMPLETE")
        elif alignment["coverage"] < MIN_ALIGNMENT_COVERAGE:
            blockers.append("ALIGNMENT_LOW_COVERAGE")

        selectable_ids = {row["material_id"] for row in self.pool._selectable(self.pool.current_band())}
        can_train = material_id in selectable_ids
        if can_train:
            status = "TRAINABLE"

        return {
            "item_id": self.item_id(batch, stem),
            "batch": batch,
            "stem": stem,
            "title": str(meta.get("title") or stem),
            "original_filename": meta.get("original_filename"),
            "audio": paths.audio.name,
            "audio_bytes": paths.audio.stat().st_size,
            "duration_seconds": row["duration_seconds"] if row else None,
            "has_transcript": has_transcript,
            "transcript_chars": len(paths.transcript.read_text(encoding="utf-8")) if paths.transcript.is_file() else 0,
            "transcript_text": paths.transcript.read_text(encoding="utf-8") if paths.transcript.is_file() else "",
            "alignment": alignment,
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "material_id": material_id,
            "material_present": row is not None,
            "ingested": bool(row and row["ingestion_status"] == "INGESTED"),
            "accent": row["accent"] if row else None,
            "license_status": row["license_status"] if row else meta.get("license_status"),
            "source_platform": platform,
            "source_course": course,
            "item_order": meta.get("item_order"),
            "difficulty_score": row["difficulty_score"] if row else None,
            "cefr_level": row["cefr_level"] if row else None,
            "can_approve": not blockers and not (row and row["ingestion_status"] == "INGESTED"),
            "can_train": can_train,
            "blockers": blockers,
        }

    def _alignment_state(self, paths: IntakePaths) -> dict[str, Any]:
        state = {
            "present": False,
            "stale": False,
            "unaligned": 0,
            "coverage": 0.0,
            "lines": 0,
            "model": None,
            "usable": False,
        }
        if not paths.alignment.is_file():
            return state
        try:
            payload = json.loads(paths.alignment.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state["present"] = True
            state["stale"] = True
            return state
        state.update(
            present=True,
            unaligned=len(payload.get("unaligned") or []),
            coverage=float(payload.get("coverage") or 0.0),
            lines=len(payload.get("lines") or []),
            model=payload.get("model"),
        )
        # The alignment is only trustworthy for the exact bytes it was made from.
        if payload.get("audio_sha256") and payload["audio_sha256"] != _digest_file(paths.audio):
            state["stale"] = True
        if paths.transcript.is_file() and payload.get("transcript_sha256"):
            if payload["transcript_sha256"] != _digest(paths.transcript.read_bytes()):
                state["stale"] = True
        state["usable"] = state["present"] and not state["stale"] and state["unaligned"] == 0 and state["coverage"] >= MIN_ALIGNMENT_COVERAGE
        return state

    def list_items(self) -> dict[str, Any]:
        # The active source is reported even when nothing is staged yet: the page
        # must be able to show "no source" versus "source active, nothing staged".
        active = active_source_platform(self.database, self.scope_id)
        if not self.inbox.is_dir():
            return {
                "active_source": active,
                "band": self.pool.current_band(),
                "counts": self._zero_counts(),
                "items": [],
            }
        items: list[dict[str, Any]] = []
        for batch_dir in sorted(p for p in self.inbox.iterdir() if p.is_dir() and p.name != "cache"):
            for audio in sorted(batch_dir.iterdir()):
                if audio.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                try:
                    items.append(self.describe(batch_dir.name, audio.stem))
                except IntakeError:
                    continue
        items.sort(key=lambda item: (item["source_course"], item["item_order"] if item["item_order"] is not None else 10**9, item["stem"]))
        counts = {
            "audio_files": len(items),
            "transcripts": sum(1 for item in items if item["has_transcript"]),
            "aligned": sum(1 for item in items if item["alignment"]["usable"]),
            "pending_review": sum(1 for item in items if item["status"] in ("NEEDS_REVIEW", "ALIGNED")),
            "ingested": sum(1 for item in items if item["ingested"]),
            "trainable": sum(1 for item in items if item["can_train"]),
        }
        return {"active_source": active, "band": self.pool.current_band(), "counts": counts, "items": items}

    @staticmethod
    def _zero_counts() -> dict[str, int]:
        return {"audio_files": 0, "transcripts": 0, "aligned": 0, "pending_review": 0, "ingested": 0, "trainable": 0}

    # ----------------------------------------------------------------- upload
    def stage_audio(
        self,
        filename: str,
        data: bytes,
        *,
        title: str | None = None,
        platform: str | None = None,
        course: str | None = None,
        item_order: int | None = None,
    ) -> dict[str, Any]:
        if not data:
            raise IntakeError("EMPTY_UPLOAD", "the uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise IntakeError("FILE_TOO_LARGE", f"audio is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        suffix = Path(filename or "").suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            raise IntakeError(
                "UNSUPPORTED_AUDIO",
                f"unsupported audio type {suffix or '(none)'}; allowed: {', '.join(AUDIO_SUFFIXES)}",
            )

        batch_dir = self.inbox / UPLOAD_BATCH
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_json = batch_dir / "batch.json"
        if not batch_json.is_file():
            batch_json.write_text(json.dumps(DEFAULT_UPLOAD_METADATA, ensure_ascii=False, indent=2), encoding="utf-8")

        digest = _digest(data)
        stem = digest[:12]
        audio_path = batch_dir / f"{stem}{suffix}"
        created = not audio_path.exists()
        if created:
            audio_path.write_bytes(data)
        else:
            # Same bytes again: keep one copy, keep the answer stable.
            pass

        # Reject undecodable files before anything is treated as staged material.
        try:
            duration = float(probe_duration(audio_path))
        except LocalImportError as error:
            if created:
                audio_path.unlink(missing_ok=True)
            raise IntakeError("UNDECODABLE_AUDIO", str(error)) from error

        meta_path = batch_dir / f"{stem}.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        meta.update(
            {
                "title": (title or meta.get("title") or Path(filename).stem or stem).strip()[:200],
                "original_filename": Path(filename).name,
                "sha256": digest,
                "duration_seconds": round(duration, 3),
                "source_platform": (platform or meta.get("source_platform") or DEFAULT_UPLOAD_METADATA["source_platform"]),
                "source_course": (course or meta.get("source_course") or DEFAULT_UPLOAD_METADATA["source_course"]),
                "uploaded_at": meta.get("uploaded_at") or datetime.now(UTC).isoformat(),
            }
        )
        if item_order is not None:
            meta["item_order"] = int(item_order)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        item = self.describe(UPLOAD_BATCH, stem)
        item["created"] = created
        return item

    # ------------------------------------------------------------- transcript
    def save_transcript(self, item_id: str, text: str) -> dict[str, Any]:
        batch, stem = self._split_item(item_id)
        paths = self._paths(batch, stem)
        lines = [line.strip() for line in str(text).replace("\r", "").split("\n")]
        lines = [line for line in lines if line]
        if not lines:
            raise IntakeError("EMPTY_TRANSCRIPT", "the transcript is empty")
        if len(lines) < 3:
            raise IntakeError("TRANSCRIPT_TOO_SHORT", "at least three sentence lines are required for three Parts")
        paths.transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Changing the words invalidates any earlier alignment and review evidence.
        if paths.alignment.is_file():
            paths.alignment.unlink()
        return self.describe(batch, stem)

    # ------------------------------------------------------------------ align
    def align(self, item_id: str, *, model: str = "small") -> dict[str, Any]:
        batch, stem = self._split_item(item_id)
        paths = self._paths(batch, stem)
        if not paths.transcript.is_file():
            raise IntakeError("TRANSCRIPT_MISSING", "save the transcript before aligning")
        if not self._model_cached(model):
            raise IntakeError(
                "MODEL_NOT_AVAILABLE",
                f"the '{model}' speech model is not cached locally; alignment will not download it silently. "
                "Fetch it once from the CLI, then retry.",
            )
        lines = [line.strip() for line in paths.transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
        try:
            words = transcribe_words(paths.audio, model_size=model)
        except ASRUnavailable as error:
            raise IntakeError("ASR_UNAVAILABLE", str(error)) from error
        alignment = align_lines(lines, words["words"])
        payload = {
            "audio": paths.audio.name,
            "model": words["model"],
            "audio_sha256": _digest_file(paths.audio),
            "transcript_sha256": _digest(paths.transcript.read_bytes()),
            **alignment.as_dict(),
        }
        paths.alignment.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        item = self.describe(batch, stem)
        item["alignment"]["lines_preview"] = payload["lines"][:50]
        return item

    @staticmethod
    def _model_cached(model: str) -> bool:
        cache = Path.home() / ".cache" / "huggingface" / "hub"
        if not cache.is_dir():
            return False
        return any(cache.glob(f"models--Systran--faster-whisper-{model}"))

    # ---------------------------------------------------------------- approve
    def approve(
        self,
        item_id: str,
        *,
        accent: str,
        license_status: str,
        confirm: bool,
        quality_ok: bool,
        transcript_verified: bool,
    ) -> dict[str, Any]:
        if not confirm:
            raise IntakeError("CONFIRMATION_REQUIRED", "approval needs an explicit confirmation")
        if accent != "en-GB":
            raise IntakeError(
                "ACCENT_NOT_ELIGIBLE",
                "only the canonical en-GB accent can enter the training library",
            )
        if str(license_status).upper() != "CLEARED":
            raise IntakeError("LICENSE_UNVERIFIED", "the licence must be cleared before approval")
        if not transcript_verified:
            raise IntakeError("TRANSCRIPT_UNVERIFIED", "the transcript must be verified before approval")
        if not quality_ok:
            raise IntakeError("QUALITY_REJECTED", "the audio quality must be accepted before approval")

        batch, stem = self._split_item(item_id)
        paths = self._paths(batch, stem)
        if not paths.transcript.is_file():
            raise IntakeError("TRANSCRIPT_MISSING", "no transcript to approve")
        state = self._alignment_state(paths)
        if not state["present"]:
            raise IntakeError("ALIGNMENT_MISSING", "align the transcript before approving")
        if state["stale"]:
            raise IntakeError("ALIGNMENT_STALE", "the alignment was made from an older audio/transcript; re-align")
        if state["unaligned"]:
            raise IntakeError(
                "ALIGNMENT_INCOMPLETE", f"{state['unaligned']} line(s) do not match the audio; fix them first"
            )
        if state["coverage"] < MIN_ALIGNMENT_COVERAGE:
            raise IntakeError("ALIGNMENT_LOW_COVERAGE", f"alignment coverage {state['coverage']} is below {MIN_ALIGNMENT_COVERAGE}")

        try:
            imported = self.local.import_one(paths.batch_dir, paths.audio)
        except LocalImportError as error:
            raise IntakeError("IMPORT_FAILED", str(error)) from error
        material_id = imported["material_id"]
        if imported.get("skipped"):
            row = self._material_row(material_id)
            if row and row["ingestion_status"] == "INGESTED":
                item = self.describe(batch, stem)
                item["already_approved"] = True
                return item

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT difficulty_score FROM materials WHERE material_id = ?", (material_id,)
            ).fetchone()
            if row is None or row["difficulty_score"] is None:
                raise IntakeError("UNSCORED", "the material has no measured difficulty score")
            connection.execute(
                "UPDATE materials SET accent = ?, license_status = ?, transcript_verified = 1,"
                "       ingestion_status = 'INGESTED' WHERE material_id = ?",
                (accent, license_status.upper(), material_id),
            )
        return self.describe(batch, stem)

    # -------------------------------------------------------------- acquisition
    def fetch_official(self) -> dict[str, Any]:
        policy = first_automation_allowed()
        database = require_database(self.settings)
        try:
            return fetch_official_pack(
                database, policy.platform, self.inbox, batch="book2-en-uk", scope_id=self.scope_id
            )
        except AcquisitionError as error:
            raise IntakeError(error.code, str(error)) from error

    def activate_source(self, *, platform: str, license_reference: str, catalog_scope: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise IntakeError("CONFIRMATION_REQUIRED", "activating a source needs an explicit confirmation")
        if not license_reference.strip() or not catalog_scope.strip():
            raise IntakeError("EVIDENCE_REQUIRED", "licence reference and catalog scope are required")
        service = MaterialCandidateService(self.database, self.settings)
        try:
            return service.activate_source(
                self.scope_id,
                platform,
                license_reference=license_reference.strip(),
                catalog_scope=catalog_scope.strip(),
                confirmed_by="USER",
            )
        except SourceGovernanceError as error:
            raise IntakeError("SOURCE_NOT_ACTIVATABLE", str(error)) from error
