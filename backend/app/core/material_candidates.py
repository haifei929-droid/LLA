"""Source-governed material discovery, review and ingestion.

`material_candidates` is the persistent discovery list: every inspected
catalog entry keeps the identity, ordering, accent, license, transcript and
review evidence that decided its disposition (pending / pending review /
rejected / retryable failure / confirmed unavailable / ingested).
`app_metadata` holds the single active source cycle per scope, its catalog
scope/cursor, the completion report and the user's explicit confirmation — no
new database and no new source adapter is introduced here.

Enforced rules (LLA source governance plan):

- no platform is active until the user confirms it together with
  authorization evidence, and a failure never falls back to another platform;
- en-US material is excluded outright; unknown/mixed accent, unverified
  license, unverified CEFR level and unverified transcript stay in review and
  never enter the formal training library;
- the only ingestible item is the next qualified item in platform order
  (CEFR A1 -> A2 -> B1, then course, then lesson order);
- discovery, review, download and ingestion states are persisted, so an
  interrupted run resumes from the persisted list instead of losing evidence;
- the whole original lesson audio is kept; the existing three-Part training
  structure and quality bar are untouched (there is no 15-20 minute gate).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.adapters.speech import WhisperASRProvider
from app.adapters.web_material import align_sentences, split_sentences
from app.config import Settings
from app.core.audio_quality import ANALYZER_VERSION, THRESHOLD_CONFIG_VERSION, AudioQualityAnalyzer
from app.core.transcript_validator import (
    OFFICIAL_ORIGIN,
    TRANSCRIPT_VERIFIED,
    encode_timestamped,
    transcript_verification,
    validate_transcript,
)
from app.db.connection import Database

#: Fixed platform order from the governance evaluation (2026-11-17). Only
#: these platforms can be activated; there is no implicit default source and
#: no cross-platform fallback.
SOURCE_ORDER: tuple[str, ...] = (
    "50LANGUAGES",
    "BRITISH_COUNCIL",
    "BREAKING_NEWS_ENGLISH",
    "BBC_LEARNING_ENGLISH",
)
SOURCE_LABELS: dict[str, str] = {
    "50LANGUAGES": "50languages / book2 (English UK)",
    "BRITISH_COUNCIL": "British Council LearnEnglish",
    "BREAKING_NEWS_ENGLISH": "Breaking News English",
    "BBC_LEARNING_ENGLISH": "BBC Learning English",
}
#: CEFR learning order; an unlabelled item is pending review, never guessed.
CEFR_ORDER: dict[str, int] = {"A1": 1, "A2": 2, "B1": 3}
#: Accents that are excluded outright (no US-English material).
EXCLUDED_ACCENTS = frozenset({"en-us", "en_us", "american", "us"})
#: Accent tokens that mean "no usable evidence yet".
UNKNOWN_ACCENTS = frozenset({"", "unknown", "mixed", "n/a", "und", "na"})
LICENSE_CLEARED = "CLEARED"

#: Candidate dispositions (persisted in material_candidates.ingestion_status).
PENDING = "PENDING"
PENDING_REVIEW = "PENDING_REVIEW"
ELIGIBLE = "ELIGIBLE"
REJECTED = "REJECTED"
RETRYABLE_FAILED = "RETRYABLE_FAILED"
UNAVAILABLE = "UNAVAILABLE"
INGESTED = "INGESTED"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
#: Dispositions that block a source switch (still processable work).
BLOCKING_DISPOSITIONS = (PENDING, ELIGIBLE, RETRYABLE_FAILED)

#: VOA program zones per difficulty stage, kept for legacy VOA-shaped
#: providers that still expose `_list_episodes` (no default source uses it).
STAGE_ZONES: dict[str, list[int]] = {
    "STAGE_1": [4456, 4791, 5254, 5535, 1581],
    "STAGE_2": [986, 1689, 955, 959, 979, 987],
    "STAGE_3": [986, 1689, 955, 959, 979, 987],
}
#: Speech-rate targets per stage, used for candidate ranking (P1 3.1).
STAGE_WPM_TARGETS: dict[str, float] = {
    "STAGE_1": 120.0,
    "STAGE_2": 150.0,
    "STAGE_3": 170.0,
}
#: Max raw entries scanned per discovery run.
PROCESS_LIMIT = 250
#: Bounded retry for provider/quality/transcription failures (P1 8).
RETRY_LIMIT = 2
RETRY_BACKOFF_SECONDS = 1.0
#: Candidate validity window (P1 parameter; versioned in config).
CANDIDATE_TTL_DAYS = 7
MIN_COVERAGE = 0.80
MIN_SENTENCES = 10

_ACTIVE_KEY = "source_governance.source.{scope}"
_REPORT_KEY = "source_governance.report.{scope}.{cycle}"
_REPORTS_KEY = "source_governance.reports.{scope}"
_CONFIRM_KEY = "source_governance.confirmation.{scope}"
_LEDGER_KEY = "source_governance.ledger.{scope}.{platform}.{cycle}"
_HISTORY_KEY = "source_governance.history.{scope}"


class SourceGovernanceError(ValueError):
    """Governance refusal with a stable code (never a silent fallback)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def normalize_accent(value: object) -> str | None:
    """Return a comparable accent token, or None when there is no evidence."""
    if value is None:
        return None
    token = re.sub(r"\s+", "", str(value)).lower().replace("_", "-")
    if token in UNKNOWN_ACCENTS:
        return None
    return token


def is_excluded_accent(value: object) -> bool:
    token = normalize_accent(value)
    return token is not None and token in EXCLUDED_ACCENTS


def normalize_license(value: object) -> str:
    return str(value or "UNKNOWN").strip().upper()


def next_platform(platform: str | None) -> str | None:
    """The platform that may follow `platform` in the fixed order."""
    if platform is None:
        return SOURCE_ORDER[0]
    if platform not in SOURCE_ORDER:
        return None
    index = SOURCE_ORDER.index(platform) + 1
    return SOURCE_ORDER[index] if index < len(SOURCE_ORDER) else None


def assess_disposition(item: dict[str, object]) -> tuple[str, str | None]:
    """Metadata-level disposition of a catalog item.

    Returns (disposition, reason_code). `ELIGIBLE` only means the discovery
    metadata is complete; audio quality and transcript verification are still
    required before ingestion.
    """
    accent = normalize_accent(item.get("accent"))
    if accent is not None and accent in EXCLUDED_ACCENTS:
        return REJECTED, "ACCENT_EXCLUDED_EN_US"
    if normalize_license(item.get("license_status")) != LICENSE_CLEARED:
        return PENDING_REVIEW, "LICENSE_UNVERIFIED"
    if accent is None:
        return PENDING_REVIEW, "ACCENT_UNVERIFIED"
    if item.get("cefr_level") not in CEFR_ORDER:
        return PENDING_REVIEW, "CEFR_UNVERIFIED"
    if not item.get("transcript_verified"):
        return PENDING_REVIEW, "TRANSCRIPT_UNVERIFIED"
    return ELIGIBLE, None


def normalize_item(entry: object) -> dict[str, object]:
    """Normalize a catalog entry: a governance dict, or a legacy 4-tuple.

    A legacy tuple only carries (article_url, mp3_url, title, duration), so the
    resulting item has no accent/CEFR/license evidence and therefore lands in
    pending review instead of being silently accepted.
    """
    if isinstance(entry, dict):
        data = dict(entry)
    else:
        url, mp3_url, title, duration = entry  # type: ignore[misc]
        data = {
            "source_url": url,
            "audio_url": mp3_url,
            "title": title,
            "duration_seconds": duration,
        }
    source_url = str(data.get("source_url") or "").strip()
    audio_url = str(data.get("audio_url") or source_url).strip()
    item_id = data.get("source_item_id") or data.get("provider_item_id")
    if not item_id:
        item_id = _item_id_from_url(source_url or audio_url)
    accent = normalize_accent(data.get("accent"))
    transcript_ready = bool(data.get("transcript_verified"))
    normalized = {
        "source_item_id": str(item_id),
        "source_url": source_url,
        "audio_url": audio_url,
        "title": str(data.get("title") or item_id),
        "duration_seconds": data.get("duration_seconds"),
        "source_course": data.get("source_course"),
        "course_order": data.get("course_order"),
        "item_order": data.get("item_order"),
        "cefr_level": data.get("cefr_level"),
        "accent": accent,
        "speaker_id": data.get("speaker_id"),
        "transcript": data.get("transcript"),
        "transcript_origin": str(data.get("transcript_origin") or "").upper() or None,
        "transcript_verified": transcript_ready,
        "license_status": normalize_license(data.get("license_status")),
        "license_reference": data.get("license_reference"),
        "accent_evidence": data.get("accent_evidence"),
        "transcript_evidence": data.get("transcript_evidence"),
    }
    return normalized


def _item_id_from_url(url: str) -> str:
    cleaned = url.rstrip("/").split("/")[-1]
    return re.sub(r"\.(html?|mp3|m4a|wav)$", "", cleaned) or url


def _course_key(row) -> tuple:
    """Platform course order: CEFR A1 -> A2 -> B1, then course, then lesson."""
    return (
        CEFR_ORDER.get(row["cefr_level"], 9),
        row["source_course"] or "",
        row["course_order"] if row["course_order"] is not None else 2**31 - 1,
        row["item_order"] if row["item_order"] is not None else 2**31 - 1,
        row["provider_item_id"],
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def active_source_platform(database: Database, scope_id: str = "default") -> str | None:
    """The active platform for a scope, if any (read-only helper)."""
    with database.connect() as connection:
        state = _read_metadata(connection, _ACTIVE_KEY.format(scope=scope_id))
    return None if state is None else str(state.get("platform"))


def _read_metadata(connection, key: str) -> object | None:
    row = connection.execute("SELECT value FROM app_metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def _write_metadata(connection, key: str, value: object) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO app_metadata(key, value) VALUES (?, ?)",
        (key, json.dumps(value, sort_keys=True)),
    )


class MaterialCandidateService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider=None,
        asr=None,
        quality: AudioQualityAnalyzer | None = None,
        now_fn: Callable[[], datetime] | None = None,
        providers: dict[str, object] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        registry: dict[str, object] = {}
        if providers:
            registry.update({str(key): value for key, value in dict(providers).items()})
        if provider is not None:
            # Injected provider for whatever platform is active; the production
            # entry point registers one adapter per authorized platform.
            registry["*"] = provider
        self.providers = registry
        self.asr = asr or WhisperASRProvider(model_size="base")
        self.quality = quality or AudioQualityAnalyzer()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    @contextmanager
    def _connection(self, connection=None) -> Iterator[object]:
        if connection is not None:
            yield connection
            return
        with self.database.connect() as owned:
            yield owned

    # ------------------------------------------------------------------
    # Active source cycle: activation, report, confirmed switch
    # ------------------------------------------------------------------

    def active_source(self, scope_id: str, connection=None) -> dict[str, object] | None:
        with self._connection(connection) as conn:
            state = _read_metadata(conn, _ACTIVE_KEY.format(scope=scope_id))
        return state if isinstance(state, dict) else None

    def source_state(self, scope_id: str) -> dict[str, object]:
        active = self.active_source(scope_id)
        with self._connection() as connection:
            confirmation = _read_metadata(connection, _CONFIRM_KEY.format(scope=scope_id))
            report = None
            ledger = None
            if active is not None:
                cycle = int(active.get("cycle", 1))
                report = _read_metadata(
                    connection, _REPORT_KEY.format(scope=scope_id, cycle=cycle)
                )
                ledger = _read_metadata(
                    connection,
                    _LEDGER_KEY.format(
                        scope=scope_id, platform=active.get("platform"), cycle=cycle
                    ),
                )
        return {
            "scope_id": scope_id,
            "active_source": active,
            "next_platform": next_platform(None if active is None else str(active["platform"])),
            "available_platforms": [
                {"platform": platform, "label": SOURCE_LABELS.get(platform, platform)}
                for platform in SOURCE_ORDER
            ],
            "last_report": None if report is None else _report_summary(report),
            "confirmation": confirmation,
            "discovery": None if ledger is None else _ledger_summary(ledger),
        }

    def activate_source(
        self,
        scope_id: str,
        platform: str,
        license_reference: str,
        catalog_scope: str,
        *,
        expected_cycle: int = 0,
        confirmed_by: str = "USER",
        note: str | None = None,
    ) -> dict[str, object]:
        """Activate the first source cycle for a scope.

        Requires an explicitly evaluated platform plus authorization and
        catalog-scope evidence. A second platform can only be activated
        through the confirmed switch flow on top of a current report.
        """
        platform = str(platform).strip().upper()
        if platform not in SOURCE_ORDER:
            raise SourceGovernanceError(
                "UNKNOWN_SOURCE", f"{platform or '<empty>'} is not an evaluated source"
            )
        if not str(license_reference).strip() or not str(catalog_scope).strip():
            raise SourceGovernanceError(
                "SOURCE_NOT_APPROVED",
                "activation requires authorization evidence and a catalog scope",
            )
        now = self.now_fn()
        with self.database.connect() as connection:
            current = self.active_source(scope_id, connection=connection)
            current_cycle = 0 if current is None else int(current.get("cycle", 1))
            if current is not None:
                raise SourceGovernanceError(
                    "SOURCE_ALREADY_ACTIVE",
                    f"{current['platform']} is active; switching needs a confirmed report",
                )
            if int(expected_cycle) != current_cycle:
                raise SourceGovernanceError(
                    "SOURCE_CYCLE_CONFLICT",
                    f"expected cycle {expected_cycle} but the current cycle is {current_cycle}",
                )
            state = {
                "platform": platform,
                "cycle": 1,
                "license_reference": str(license_reference),
                "catalog_scope": str(catalog_scope),
                "activated_at": now.isoformat(),
                "confirmed_by": confirmed_by,
                "note": note,
                "previous_platform": None,
            }
            _write_metadata(connection, _ACTIVE_KEY.format(scope=scope_id), state)
            history = _read_metadata(connection, _HISTORY_KEY.format(scope=scope_id)) or []
            history.append(
                {
                    "event": "SOURCE_ACTIVATED",
                    "platform": platform,
                    "cycle": 1,
                    "at": now.isoformat(),
                    "license_reference": str(license_reference),
                    "catalog_scope": str(catalog_scope),
                    "confirmed_by": confirmed_by,
                }
            )
            _write_metadata(connection, _HISTORY_KEY.format(scope=scope_id), history[-20:])
        return state

    def report(self, scope_id: str) -> dict[str, object]:
        """Generate and persist the completion report for the current cycle.

        Coverage, classification, legal acquisition, ingestion and the
        remaining items are reported separately: discovering the whole catalog
        never means legally acquiring or ingesting all of it.
        """
        active = self.active_source(scope_id)
        if active is None:
            raise SourceGovernanceError("SOURCE_NOT_APPROVED", "no active source to report on")
        platform = str(active["platform"])
        cycle = int(active.get("cycle", 1))
        now = self.now_fn()
        with self.database.connect() as connection:
            ledger = _read_metadata(
                connection, _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle)
            ) or _empty_ledger(scope_id, platform, cycle)
            rows = connection.execute(
                """
                SELECT ingestion_status, COUNT(*) AS n
                  FROM material_candidates
                 WHERE scope_id = ? AND provider = ?
                 GROUP BY ingestion_status
                """,
                (scope_id, platform),
            ).fetchall()
            report = _build_report(ledger, scope_id, platform, cycle, now.isoformat())
            _write_metadata(
                connection, _REPORT_KEY.format(scope=scope_id, cycle=cycle), report
            )
            report_ids = _read_metadata(connection, _REPORTS_KEY.format(scope=scope_id)) or []
            report_ids = [report["report_id"], *[rid for rid in report_ids if rid != report["report_id"]]][:10]
            _write_metadata(connection, _REPORTS_KEY.format(scope=scope_id), report_ids)
            report["ingestion_rows"] = {row["ingestion_status"]: row["n"] for row in rows}
        return report

    def confirm_switch(
        self,
        scope_id: str,
        report_id: str,
        next_source: str,
        license_reference: str,
        catalog_scope: str,
        *,
        acknowledge_pending: bool = False,
        confirmed_by: str = "USER",
    ) -> dict[str, object]:
        """Switch to the next platform only on an explicit, current confirmation.

        The confirmation is bound to one report version: a regenerated report
        (new discovery, changed disposition) invalidates the old confirmation.
        """
        platform = str(next_source).strip().upper()
        if platform not in SOURCE_ORDER:
            raise SourceGovernanceError(
                "UNKNOWN_SOURCE", f"{platform or '<empty>'} is not an evaluated source"
            )
        if not str(license_reference).strip() or not str(catalog_scope).strip():
            raise SourceGovernanceError(
                "SOURCE_NOT_APPROVED",
                "switching requires authorization evidence for the next platform",
            )
        now = self.now_fn()
        with self.database.connect() as connection:
            active = self.active_source(scope_id, connection=connection)
            if active is None:
                raise SourceGovernanceError("SOURCE_NOT_APPROVED", "no active source to switch from")
            current_platform = str(active["platform"])
            cycle = int(active.get("cycle", 1))
            expected = next_platform(current_platform)
            if platform != expected:
                raise SourceGovernanceError(
                    "SOURCE_ORDER_VIOLATION",
                    f"the next platform in the fixed order is {expected}, not {platform}",
                )
            report = _read_metadata(
                connection, _REPORT_KEY.format(scope=scope_id, cycle=cycle)
            )
            if not isinstance(report, dict) or report.get("report_id") != report_id:
                raise SourceGovernanceError(
                    "CONFIRMATION_STALE",
                    "the confirmed report is missing or has been regenerated",
                )
            if report.get("catalog_complete") is not True or report.get("has_cursor"):
                raise SourceGovernanceError(
                    "SOURCE_CATALOG_INCOMPLETE",
                    "the catalog snapshot is incomplete; completion cannot be reported",
                )
            if report.get("retryable_failures") or report.get("qualified_unprocessed"):
                raise SourceGovernanceError(
                    "SOURCE_ITEMS_PENDING",
                    "retryable failures or unprocessed qualified items remain",
                )
            if report.get("pending_manual_items") and not acknowledge_pending:
                raise SourceGovernanceError(
                    "PENDING_ITEMS_REQUIRE_DECISION",
                    "items awaiting manual confirmation must be explicitly acknowledged",
                )
            state = {
                "platform": platform,
                "cycle": cycle + 1,
                "license_reference": str(license_reference),
                "catalog_scope": str(catalog_scope),
                "activated_at": now.isoformat(),
                "confirmed_by": confirmed_by,
                "note": None,
                "previous_platform": current_platform,
                "previous_cycle": cycle,
                "confirmed_report_id": report_id,
            }
            _write_metadata(connection, _ACTIVE_KEY.format(scope=scope_id), state)
            _write_metadata(
                connection,
                _CONFIRM_KEY.format(scope=scope_id),
                {
                    "report_id": report_id,
                    "from_platform": current_platform,
                    "to_platform": platform,
                    "cycle": cycle + 1,
                    "confirmed_at": now.isoformat(),
                    "confirmed_by": confirmed_by,
                    "acknowledged_pending_items": report.get("pending_manual_items", []),
                },
            )
            history = _read_metadata(connection, _HISTORY_KEY.format(scope=scope_id)) or []
            history.append(
                {
                    "event": "SOURCE_SWITCH_CONFIRMED",
                    "platform": platform,
                    "previous_platform": current_platform,
                    "cycle": cycle + 1,
                    "at": now.isoformat(),
                    "report_id": report_id,
                    "confirmed_by": confirmed_by,
                }
            )
            _write_metadata(connection, _HISTORY_KEY.format(scope=scope_id), history[-20:])
        return state

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def search(
        self,
        *,
        scope_id: str,
        speed_stage: str = "STAGE_1",
        target_duration_min: float | None = None,
        target_duration_max: float | None = None,
        max_results: int = 3,
    ) -> dict[str, object]:
        """Run one governed discovery pass over the active platform.

        There is no 15-20 minute gate: an explicit band is honoured when the
        caller passes one, otherwise every original lesson length is allowed
        and the existing audio-quality bar still applies.
        """
        if speed_stage not in STAGE_WPM_TARGETS:
            raise ValueError(f"unsupported speed_stage: {speed_stage}")
        if not 1 <= max_results <= 3:
            raise ValueError("max_results must be between 1 and 3")
        if (
            target_duration_min is not None
            and target_duration_max is not None
            and target_duration_max <= target_duration_min
        ):
            raise ValueError("target_duration_max must exceed target_duration_min")
        lower = None if target_duration_min is None else target_duration_min * 60.0
        upper = None if target_duration_max is None else target_duration_max * 60.0

        active = self.active_source(scope_id)
        if active is None:
            raise SourceGovernanceError(
                "SOURCE_NOT_APPROVED",
                "no active material source; confirm a platform before discovery",
            )
        platform = str(active["platform"])
        cycle = int(active.get("cycle", 1))
        provider = self.providers.get(platform) or self.providers.get("*")
        if provider is None:
            raise SourceGovernanceError(
                "SOURCE_NOT_APPROVED",
                f"no authorized adapter is registered for {platform}",
            )

        batch_id = str(uuid4())
        now = self.now_fn()
        rejected: dict[str, int] = {}
        candidates: list[dict[str, object]] = []
        pending: list[dict[str, object]] = []
        quarantined: list[dict[str, object]] = []
        work_dir = self.settings.processed_dir / "p1-candidates" / batch_id
        ledger = self._load_ledger(scope_id, platform, cycle, provider)

        for entry in self._provider_items(provider, speed_stage)[:PROCESS_LIMIT]:
            item = normalize_item(entry)
            item_id = str(item["source_item_id"])
            disposition, reason = assess_disposition(item)
            duration = item.get("duration_seconds")
            if disposition in (ELIGIBLE, PENDING_REVIEW):
                if duration is None:
                    rejected["duration_missing"] = rejected.get("duration_missing", 0) + 1
                    disposition, reason = OUT_OF_SCOPE, "DURATION_MISSING"
                elif not _duration_in_band(float(duration), lower, upper):
                    rejected["duration_out_of_range"] = rejected.get("duration_out_of_range", 0) + 1
                    disposition, reason = OUT_OF_SCOPE, "DURATION_OUT_OF_SCOPE"
            if disposition == REJECTED:
                key = "accent_excluded" if reason == "ACCENT_EXCLUDED_EN_US" else "quarantined"
                rejected[key] = rejected.get(key, 0) + 1
                self._record_item(ledger, item, REJECTED, reason, batch_id, now)
                quarantined.append(_public_item(item, REJECTED, reason))
                continue
            if disposition == OUT_OF_SCOPE:
                self._record_item(ledger, item, OUT_OF_SCOPE, reason, batch_id, now)
                continue

            prior = (ledger.get("items") or {}).get(item_id) or {}
            if prior.get("disposition") == INGESTED:
                rejected["duplicate"] = rejected.get("duplicate", 0) + 1
                continue
            fingerprint = hashlib.sha256(str(item["source_url"]).encode()).hexdigest()[:32]
            existing = self._candidate_record(platform, item_id, scope_id, fingerprint)
            if existing is not None:
                # Already known (a re-run after a crash): keep its persisted
                # disposition in the ledger instead of dropping the item.
                rejected["duplicate"] = rejected.get("duplicate", 0) + 1
                self._record_item(
                    ledger,
                    item,
                    str(existing["ingestion_status"]),
                    existing["failure_code"],
                    batch_id,
                    now,
                    downloaded=bool(existing["audio_path"]),
                )
                continue

            try:
                audio_path = self._fetch_audio(provider, item, work_dir)
            except Exception:
                rejected["download_failed"] = rejected.get("download_failed", 0) + 1
                self._record_item(ledger, item, RETRYABLE_FAILED, "DOWNLOAD_FAILED", batch_id, now)
                continue

            quality = self._analyze_audio(audio_path)
            if quality is None:
                rejected["quality_failed"] = rejected.get("quality_failed", 0) + 1
                self._record_item(ledger, item, RETRYABLE_FAILED, "AUDIO_UNREADABLE", batch_id, now)
                continue
            if quality.level == "Poor":
                rejected["quality_poor"] = rejected.get("quality_poor", 0) + 1
                self._record_item(ledger, item, REJECTED, "QUALITY_POOR", batch_id, now)
                continue
            if not _duration_in_band(quality.duration_seconds, lower, upper):
                rejected["duration_out_of_range"] = rejected.get("duration_out_of_range", 0) + 1
                self._record_item(ledger, item, OUT_OF_SCOPE, "DURATION_OUT_OF_SCOPE", batch_id, now)
                continue

            try:
                segments = self._retry(lambda: self.asr.transcribe(str(audio_path)))
            except Exception:
                rejected["transcribe_failed"] = rejected.get("transcribe_failed", 0) + 1
                self._record_item(ledger, item, RETRYABLE_FAILED, "ASR_FAILED", batch_id, now)
                continue

            transcript, aligned = self._transcript_for(item, segments)
            validation = validate_transcript(
                transcript, aligned, quality.duration_seconds, MIN_COVERAGE, MIN_SENTENCES
            )
            if validation.status != "COMPLETE":
                key = f"transcript_{validation.status.lower()}"
                rejected[key] = rejected.get(key, 0) + 1
                self._record_item(
                    ledger, item, REJECTED, f"TRANSCRIPT_{validation.status}", batch_id, now
                )
                continue

            verification = transcript_verification(
                validation,
                item.get("transcript_origin"),
                bool(item.get("transcript_verified")),
            )
            if disposition == ELIGIBLE and verification == TRANSCRIPT_VERIFIED:
                final_disposition, final_reason = ELIGIBLE, None
            else:
                final_disposition = PENDING_REVIEW
                final_reason = reason or "TRANSCRIPT_UNVERIFIED"

            words = len(transcript.split())
            wpm = (words / (quality.duration_seconds / 60.0)) if quality.duration_seconds else 0.0
            candidate_id = str(uuid4())
            stored = self._store_candidate(
                candidate_id=candidate_id,
                scope_id=scope_id,
                platform=platform,
                item=item,
                transcript=transcript,
                aligned=aligned,
                duration_seconds=quality.duration_seconds,
                speed_stage=speed_stage,
                quality=quality,
                report_id=str(uuid4()),
                transcript_status=validation.status,
                batch_id=batch_id,
                fingerprint=fingerprint,
                audio_path=audio_path,
                disposition=final_disposition,
                reason=final_reason,
                now=now,
            )
            if not stored:
                rejected["duplicate"] = rejected.get("duplicate", 0) + 1
                continue
            self._record_item(
                ledger, item, final_disposition, final_reason, batch_id, now, downloaded=True
            )
            payload = {
                "candidate_id": candidate_id,
                "provider": platform,
                "provider_item_id": item_id,
                "title": item["title"],
                "duration_seconds": round(quality.duration_seconds, 1),
                "speed_stage": speed_stage,
                "audio_quality": quality.level,
                "transcript_status": validation.status,
                "candidate_status": "CANDIDATE",
                "speech_rate_wpm": round(wpm, 1),
                "cefr_level": item.get("cefr_level"),
                "accent": item.get("accent"),
                "source_course": item.get("source_course"),
                "item_order": item.get("item_order"),
                "ingestion_status": final_disposition,
                "reason_code": final_reason,
            }
            if final_disposition == ELIGIBLE:
                candidates.append(payload)
            else:
                pending.append(payload)
            if len(candidates) >= max_results:
                break

        ranked = self._rank(
            candidates,
            target_duration_min if target_duration_min is not None else 0.0,
            target_duration_max
            if target_duration_max is not None
            else max(
                (float(candidate["duration_seconds"]) / 60.0 for candidate in candidates),
                default=1.0,
            ),
            speed_stage,
        )
        self._write_ledger(scope_id, platform, cycle, ledger, now)
        self._write_search_audit(batch_id, scope_id, speed_stage, ranked, rejected, now, platform)
        qualified = self.next_qualified(scope_id)
        return {
            "search_batch_id": batch_id,
            "scope_id": scope_id,
            "speed_stage": speed_stage,
            "active_source": {"platform": platform, "cycle": cycle},
            "candidates": ranked,
            "pending_review": pending,
            "quarantined": quarantined,
            "rejection_summary": rejected,
            "catalog": {
                "catalog_complete": bool(ledger.get("catalog_complete")),
                "catalog_scope": ledger.get("catalog_scope"),
                "known_total": ledger.get("known_total"),
                "has_cursor": bool(ledger.get("has_cursor")),
            },
            "next_qualified_candidate_id": None if qualified is None else qualified["candidate_id"],
        }

    def list_candidates(
        self, scope_id: str, disposition: str | None = None
    ) -> list[dict[str, object]]:
        """Persisted discovery list for the UI.

        Transcript text is deliberately not part of this payload: a blind
        review surface must not leak the answer text.
        """
        query = """
            SELECT candidate_id, provider, provider_item_id, title, source_url, duration_seconds,
                   speed_stage, audio_quality, transcript_status, candidate_status,
                   ingestion_status, failure_code, cefr_level, accent, speaker_id,
                   source_course, course_order, item_order, license_status,
                   transcript_verified, reviewed_by, reviewed_at, attempt_count,
                   created_at, expires_at
              FROM material_candidates
             WHERE scope_id = ?
        """
        params: list[object] = [scope_id]
        if disposition is not None:
            query += " AND ingestion_status = ?"
            params.append(disposition)
        query += " ORDER BY created_at, candidate_id"
        with self.database.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        listing = [dict(row) for row in rows]
        # The panel shows the platform course order; the UI order is not the
        # ingestion authority (prepare re-checks it), but showing it avoids
        # misleading the user about what comes next.
        listing.sort(key=_course_key)
        return listing

    def review_candidate(
        self,
        candidate_id: str,
        scope_id: str,
        *,
        decision: str,
        accent: str | None = None,
        transcript_verified: bool | None = None,
        reviewer: str = "USER",
        note: str | None = None,
    ) -> dict[str, object]:
        """Explicit human confirmation (approve) or rejection of a candidate.

        Approval requires confirmed non-US accent, cleared license, known CEFR
        level and verified transcript evidence; without them the item stays out
        of the formal training library.
        """
        decision = str(decision).strip().upper()
        if decision not in ("APPROVE", "REJECT"):
            raise ValueError("decision must be APPROVE or REJECT")
        active = self.active_source(scope_id)
        if active is None:
            raise SourceGovernanceError("SOURCE_NOT_APPROVED", "no active source to review against")
        platform = str(active["platform"])
        cycle = int(active.get("cycle", 1))
        now = self.now_fn()
        with self.database.connect() as connection:
            candidate = connection.execute(
                "SELECT * FROM material_candidates WHERE candidate_id = ? AND scope_id = ?",
                (candidate_id, scope_id),
            ).fetchone()
            if candidate is None:
                raise SourceGovernanceError(
                    "CANDIDATE_NOT_FOUND", f"no candidate {candidate_id} in scope {scope_id}"
                )
            if candidate["provider"] != platform:
                raise SourceGovernanceError(
                    "SOURCE_NOT_APPROVED", "candidate belongs to a non-active source"
                )
            updates: dict[str, object] = {
                "reviewed_by": reviewer,
                "reviewed_at": now.isoformat(),
            }
            if decision == "REJECT":
                status, reason = REJECTED, "REVIEW_REJECTED"
            else:
                confirmed_accent = normalize_accent(
                    accent if accent is not None else candidate["accent"]
                )
                if confirmed_accent is None or confirmed_accent in EXCLUDED_ACCENTS:
                    raise SourceGovernanceError(
                        "ACCENT_NOT_ELIGIBLE",
                        "approval requires a confirmed non-US accent with evidence",
                    )
                if normalize_license(candidate["license_status"]) != LICENSE_CLEARED:
                    raise SourceGovernanceError(
                        "LICENSE_UNVERIFIED", "approval requires cleared source license"
                    )
                if candidate["cefr_level"] not in CEFR_ORDER:
                    raise SourceGovernanceError(
                        "CEFR_UNVERIFIED", "approval requires a verified CEFR level"
                    )
                verified = (
                    bool(candidate["transcript_verified"])
                    if transcript_verified is None
                    else bool(transcript_verified)
                )
                if not verified:
                    raise SourceGovernanceError(
                        "TRANSCRIPT_UNVERIFIED",
                        "approval requires verified transcript evidence",
                    )
                if candidate["audio_quality"] == "Poor":
                    raise SourceGovernanceError("QUALITY_POOR", "approval requires non-poor audio")
                status, reason = ELIGIBLE, None
                updates.update(
                    {
                        "accent": confirmed_accent,
                        "accent_evidence": candidate["accent_evidence"] or f"review:{reviewer}",
                        "transcript_verified": 1,
                        "transcript_evidence": candidate["transcript_evidence"]
                        or f"review:{reviewer}",
                        "failure_code": None,
                    }
                )
            updates["ingestion_status"] = status
            updates["failure_code"] = None if status == ELIGIBLE else f"REVIEW_{decision}"
            assignments = ", ".join(f"{column} = ?" for column in updates)
            connection.execute(
                f"UPDATE material_candidates SET {assignments} WHERE candidate_id = ? AND scope_id = ?",
                (*updates.values(), candidate_id, scope_id),
            )
            ledger = _read_metadata(
                connection, _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle)
            ) or _empty_ledger(scope_id, platform, cycle)
            record = (ledger.get("items") or {}).get(candidate["provider_item_id"]) or {}
            record.update(
                {
                    "disposition": status,
                    "reason_code": reason,
                    "reviewed_by": reviewer,
                    "reviewed_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
            ledger.setdefault("items", {})[candidate["provider_item_id"]] = record
            _write_metadata(
                connection,
                _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle),
                ledger,
            )
        return {
            "candidate_id": candidate_id,
            "ingestion_status": status,
            "reason_code": reason,
            "reviewed_by": reviewer,
            "reviewed_at": now.isoformat(),
            "note": note,
        }

    def mark_ingested(
        self,
        connection,
        *,
        scope_id: str,
        platform: str,
        item_id: str,
        material_id: str,
        now: datetime,
    ) -> None:
        """Record the ingestion in the persisted discovery ledger.

        Called inside the ingestion transaction, so the ledger state and the
        formal material are committed together.
        """
        active = self.active_source(scope_id, connection=connection)
        if active is None:
            return
        cycle = int(active.get("cycle", 1))
        ledger = _read_metadata(
            connection, _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle)
        ) or _empty_ledger(scope_id, platform, cycle)
        record = (ledger.get("items") or {}).get(item_id) or {}
        record.update(
            {
                "disposition": INGESTED,
                "reason_code": None,
                "material_id": material_id,
                "downloaded": True,
                "updated_at": now.isoformat(),
            }
        )
        ledger.setdefault("items", {})[item_id] = record
        ledger["updated_at"] = now.isoformat()
        _write_metadata(
            connection,
            _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle),
            ledger,
        )

    def next_qualified(self, scope_id: str, connection=None) -> dict[str, object] | None:
        """The single next ingestible item, in platform course order."""
        with self._connection(connection) as conn:
            active = self.active_source(scope_id, connection=conn)
            if active is None:
                return None
            rows = conn.execute(
                """
                SELECT * FROM material_candidates
                 WHERE scope_id = ? AND provider = ? AND ingestion_status = ?
                   AND candidate_status IN ('CANDIDATE', 'SELECTED')
                """,
                (scope_id, str(active["platform"]), ELIGIBLE),
            ).fetchall()
        now = self.now_fn()
        usable = [
            row
            for row in rows
            if (expiry := _parse_timestamp(row["expires_at"])) is None or expiry >= now
        ]
        if not usable:
            return None
        usable.sort(key=_course_key)
        return dict(usable[0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _provider_items(self, provider, speed_stage: str) -> list[object]:
        catalog = getattr(provider, "catalog_items", None)
        if callable(catalog):
            return list(catalog())
        episodes = getattr(provider, "_list_episodes", None)
        if callable(episodes):
            try:
                return list(episodes(zone_ids=STAGE_ZONES.get(speed_stage, [])))
            except TypeError:
                return list(episodes())
        raise SourceGovernanceError(
            "PROVIDER_UNSUPPORTED",
            f"{type(provider).__name__} exposes neither catalog_items nor _list_episodes",
        )

    def _load_ledger(self, scope_id: str, platform: str, cycle: int, provider) -> dict[str, object]:
        with self._connection() as connection:
            stored = _read_metadata(
                connection, _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle)
            )
        ledger = stored if isinstance(stored, dict) else _empty_ledger(scope_id, platform, cycle)
        ledger.setdefault("items", {})
        declared = self._catalog_info(provider)
        if declared is not None:
            ledger.update(declared)
        return ledger

    @staticmethod
    def _catalog_info(provider) -> dict[str, object] | None:
        """Provider-declared catalog snapshot; undeclared means 'unknown'."""
        declared = getattr(provider, "catalog_info", None)
        if not callable(declared):
            return None
        try:
            raw = declared() or {}
        except TypeError:
            return None
        if not isinstance(raw, dict):
            return None
        known_total = raw.get("known_total")
        return {
            "catalog_complete": bool(raw.get("catalog_complete")),
            "known_total": None if known_total is None else int(known_total),
            "has_cursor": bool(raw.get("has_cursor", False)),
            "catalog_scope": raw.get("catalog_scope"),
        }

    def _write_ledger(
        self, scope_id: str, platform: str, cycle: int, ledger: dict[str, object], now: datetime
    ) -> None:
        ledger["updated_at"] = now.isoformat()
        with self._connection() as connection:
            _write_metadata(
                connection,
                _LEDGER_KEY.format(scope=scope_id, platform=platform, cycle=cycle),
                ledger,
            )

    @staticmethod
    def _record_item(
        ledger: dict[str, object],
        item: dict[str, object],
        disposition: str,
        reason: str | None,
        batch_id: str,
        now: datetime,
        downloaded: bool = False,
    ) -> None:
        items = ledger.setdefault("items", {})
        item_id = str(item["source_item_id"])
        prior = items.get(item_id) or {}
        items[item_id] = {
            "title": item.get("title"),
            "source_url": item.get("source_url"),
            "source_course": item.get("source_course"),
            "course_order": item.get("course_order"),
            "item_order": item.get("item_order"),
            "cefr_level": item.get("cefr_level"),
            "accent": item.get("accent"),
            "disposition": disposition,
            "reason_code": reason,
            "batch_id": batch_id,
            "downloaded": bool(downloaded or prior.get("downloaded")),
            "updated_at": now.isoformat(),
        }

    def _fetch_audio(self, provider, item: dict[str, object], work_dir: Path) -> Path:
        """Download into a staging area and publish atomically.

        A half-written file can never be mistaken for a usable download: the
        bytes stay in staging until the boundary checks pass, then a single
        rename publishes the file.
        """
        staging = work_dir / "_staging"
        staging.mkdir(parents=True, exist_ok=True)
        audio_id = _safe_audio_id(str(item["source_item_id"]))
        downloaded = Path(
            self._retry(
                lambda: provider.download_audio(
                    str(item["audio_url"]), audio_id, staging
                )
            )
        )
        if not downloaded.is_file() or downloaded.stat().st_size == 0:
            downloaded.unlink(missing_ok=True)
            raise RuntimeError(f"downloaded audio is missing or empty for {item['source_item_id']}")
        work_dir.mkdir(parents=True, exist_ok=True)
        published = work_dir / downloaded.name
        os.replace(downloaded, published)
        return published

    def _analyze_audio(self, audio_path: Path):
        try:
            quality = self._retry(lambda: self.quality.analyze(str(audio_path)))
        except Exception:
            return None
        if quality.failure_code:
            return None
        return quality

    @staticmethod
    def _transcript_for(item: dict[str, object], segments) -> tuple[str, list[tuple[str, float, float]]]:
        official = str(item.get("transcript") or "").strip()
        if str(item.get("transcript_origin") or "").upper() == OFFICIAL_ORIGIN and official:
            transcript = official
        else:
            transcript = " ".join(segment.text for segment in segments).strip()
        sentences = split_sentences(transcript) if transcript else []
        return transcript, align_sentences(sentences, segments) if sentences else []

    def _write_search_audit(
        self,
        batch_id: str,
        scope_id: str,
        speed_stage: str,
        candidates: list[dict[str, object]],
        rejected: dict[str, int],
        now: datetime,
        platform: str,
    ) -> None:
        """Persist the search evidence (P1 9): batch, platform, versions,
        outcome and rejection reasons, so filtering decisions stay auditable."""
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO search_audits(
                    batch_id, scope_id, speed_stage, provider, analyzer_version,
                    threshold_config_version, candidate_count, rejection_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, scope_id, speed_stage, platform,
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

    def _rank(
        self,
        candidates: list[dict[str, object]],
        duration_min: float,
        duration_max: float,
        speed_stage: str,
    ) -> list[dict[str, object]]:
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

    def _store_candidate(
        self,
        *,
        candidate_id: str,
        scope_id: str,
        platform: str,
        item: dict[str, object],
        transcript: str,
        aligned: list[tuple[str, float, float]],
        duration_seconds: float,
        speed_stage: str,
        quality,
        report_id: str,
        transcript_status: str,
        batch_id: str,
        fingerprint: str,
        audio_path: Path,
        disposition: str,
        reason: str | None,
        now: datetime,
    ) -> bool:
        expires_at = now + timedelta(days=CANDIDATE_TTL_DAYS)
        params = {
            "candidate_id": candidate_id,
            "scope_id": scope_id,
            "platform": platform,
            "item_id": str(item["source_item_id"]),
            "title": str(item["title"]),
            "source_url": str(item["source_url"]),
            "audio_url": str(item["audio_url"]),
            "transcript": transcript,
            "duration": duration_seconds,
            "speed_stage": speed_stage,
            "quality": quality.level,
            "report_id": report_id,
            "transcript_status": transcript_status,
            "batch_id": batch_id,
            "fingerprint": fingerprint,
            "audio_path": str(audio_path),
            "aligned": encode_timestamped(aligned),
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "source_course": item.get("source_course"),
            "course_order": item.get("course_order"),
            "item_order": item.get("item_order"),
            "accent": item.get("accent"),
            "speaker_id": item.get("speaker_id"),
            "cefr_level": item.get("cefr_level"),
            "transcript_verified": 1 if item.get("transcript_verified") else 0,
            "license_status": normalize_license(item.get("license_status")),
            "license_reference": item.get("license_reference"),
            "ingestion_status": disposition,
            "source_priority": _platform_priority(platform),
            "transcript_origin": item.get("transcript_origin"),
            "accent_evidence": item.get("accent_evidence"),
            "transcript_evidence": item.get("transcript_evidence"),
            "failure_code": reason,
        }
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO material_candidates(
                        candidate_id, scope_id, provider, provider_item_id, title, source_url,
                        audio_url, transcript, duration_seconds, speed_stage, audio_quality,
                        audio_quality_report_id, transcript_status, candidate_status,
                        search_batch_id, content_fingerprint, audio_path,
                        timestamped_sentences_json, created_at, expires_at,
                        source_course, course_order, item_order, accent, speaker_id, cefr_level,
                        transcript_verified, license_status, license_reference, ingestion_status,
                        source_priority, transcript_origin, accent_evidence, transcript_evidence,
                        failure_code
                    ) VALUES (
                        :candidate_id, :scope_id, :platform, :item_id, :title, :source_url,
                        :audio_url, :transcript, :duration, :speed_stage, :quality,
                        :report_id, :transcript_status, 'CANDIDATE',
                        :batch_id, :fingerprint, :audio_path,
                        :aligned, :created_at, :expires_at,
                        :source_course, :course_order, :item_order, :accent, :speaker_id, :cefr_level,
                        :transcript_verified, :license_status, :license_reference, :ingestion_status,
                        :source_priority, :transcript_origin, :accent_evidence, :transcript_evidence,
                        :failure_code
                    )
                    """,
                    params,
                )
                connection.execute(
                    """
                    INSERT INTO audio_quality_reports(
                        report_id, candidate_id, audio_fingerprint, quality_level, metrics_json,
                        threshold_config_version, analyzer_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id, candidate_id, quality.fingerprint, quality.level,
                        _quality_metrics(quality), THRESHOLD_CONFIG_VERSION, ANALYZER_VERSION,
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            # A duplicate identity (alias URL, another batch, another scope) is
            # not an error: the persisted list keeps the first record.
            return False
        return True

    def _candidate_record(self, platform: str, item_id: str, scope_id: str, fingerprint: str):
        """Persisted record for this source identity (or this URL alias).

        Identity is (platform, item id) regardless of URL, batch or scope; the
        per-scope content fingerprint is the fallback so an alias URL of the
        same resource cannot create a second record.
        """
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT ingestion_status, failure_code, audio_path
                  FROM material_candidates
                 WHERE provider = ? AND provider_item_id = ?
                 LIMIT 1
                """,
                (platform, item_id),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT ingestion_status, failure_code, audio_path
                      FROM material_candidates
                     WHERE scope_id = ? AND content_fingerprint = ?
                     LIMIT 1
                    """,
                    (scope_id, fingerprint),
                ).fetchone()
        return row


def _platform_priority(platform: str) -> int:
    return SOURCE_ORDER.index(platform) + 1 if platform in SOURCE_ORDER else 99


def _duration_in_band(duration: float, lower: float | None, upper: float | None) -> bool:
    if lower is not None and duration < lower:
        return False
    if upper is not None and duration > upper:
        return False
    return True


def _safe_audio_id(item_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", item_id).strip("-")[:60] or "item"
    return f"{cleaned}-{uuid4().hex[:8]}"


def _public_item(item: dict[str, object], disposition: str, reason: str | None) -> dict[str, object]:
    """Metadata-only view of a quarantined item: no transcript text."""
    return {
        "provider_item_id": item.get("source_item_id"),
        "title": item.get("title"),
        "source_url": item.get("source_url"),
        "source_course": item.get("source_course"),
        "item_order": item.get("item_order"),
        "cefr_level": item.get("cefr_level"),
        "accent": item.get("accent"),
        "ingestion_status": disposition,
        "reason_code": reason,
    }


def _empty_ledger(scope_id: str, platform: str, cycle: int) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "platform": platform,
        "cycle": cycle,
        # Undeclared snapshots are unknown: completion is never claimed and
        # ratios are reported as unknown rather than as 100%.
        "catalog_complete": False,
        "known_total": None,
        "has_cursor": True,
        "catalog_scope": None,
        "items": {},
        "updated_at": None,
    }


def _build_report(
    ledger: dict[str, object], scope_id: str, platform: str, cycle: int, generated_at: str
) -> dict[str, object]:
    items = ledger.get("items") or {}
    dispositions: dict[str, int] = {}
    downloaded = 0
    for record in items.values():
        disposition = str(record.get("disposition"))
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        if record.get("downloaded"):
            downloaded += 1
    discovered = len(items)
    known_total = ledger.get("known_total")
    pending_manual = [
        item_id
        for item_id, record in items.items()
        if record.get("disposition") == PENDING_REVIEW
    ]
    retryable = [
        item_id
        for item_id, record in items.items()
        if record.get("disposition") == RETRYABLE_FAILED
    ]
    qualified_unprocessed = [
        item_id for item_id, record in items.items() if record.get("disposition") == ELIGIBLE
    ]
    pending_processing = [
        item_id for item_id, record in items.items() if record.get("disposition") == PENDING
    ]
    blocking = retryable + qualified_unprocessed + pending_processing
    catalog_complete = bool(ledger.get("catalog_complete"))
    has_cursor = bool(ledger.get("has_cursor"))
    if not catalog_complete or has_cursor:
        completion_status = "INCOMPLETE"
    elif known_total is None:
        completion_status = "UNKNOWN_TOTAL"
    elif blocking:
        completion_status = "INCOMPLETE"
    else:
        completion_status = "COMPLETE"
    return {
        "report_id": str(uuid4()),
        "scope_id": scope_id,
        "platform": platform,
        "cycle": cycle,
        "generated_at": generated_at,
        "catalog_complete": catalog_complete,
        "catalog_scope": ledger.get("catalog_scope"),
        "has_cursor": has_cursor,
        "known_total": known_total,
        "discovered": discovered,
        "discovered_ratio": (discovered / known_total) if known_total else None,
        "dispositions": dispositions,
        "classified_ratio": (
            (discovered - len(pending_processing)) / discovered if discovered else None
        ),
        "legal_acquisition_ratio": (downloaded / discovered) if discovered else None,
        "ingested_ratio": (dispositions.get(INGESTED, 0) / discovered) if discovered else None,
        "remaining_items": len(blocking),
        "pending_manual_items": pending_manual,
        "retryable_failures": retryable,
        "qualified_unprocessed": qualified_unprocessed,
        "completion_status": completion_status,
    }


def _report_summary(report: dict[str, object]) -> dict[str, object]:
    keys = (
        "report_id",
        "generated_at",
        "cycle",
        "completion_status",
        "catalog_complete",
        "discovered",
        "known_total",
        "remaining_items",
        "pending_manual_items",
        "retryable_failures",
    )
    return {key: report.get(key) for key in keys}


def _ledger_summary(ledger: dict[str, object]) -> dict[str, object]:
    items = ledger.get("items") or {}
    dispositions: dict[str, int] = {}
    for record in items.values():
        disposition = str(record.get("disposition"))
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
    return {
        "discovered": len(items),
        "dispositions": dispositions,
        "catalog_complete": bool(ledger.get("catalog_complete")),
        "has_cursor": bool(ledger.get("has_cursor")),
        "known_total": ledger.get("known_total"),
        "catalog_scope": ledger.get("catalog_scope"),
        "updated_at": ledger.get("updated_at"),
    }


def _quality_metrics(quality) -> str:
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
