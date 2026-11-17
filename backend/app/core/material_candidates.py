"""Source-governed material discovery, review and ingestion.

`material_candidates` is the single persistent discovery list *and* the single
source of truth for every entry's state: a discovered entry, a rejected one, a
retryable failure, a reviewed item and an ingested material all live in that
one table together with the identity, ordering, accent, license, transcript and
review evidence that decided them. `app_metadata` only holds the one active
source cycle (with its catalog snapshot), the completion report and the user's
explicit confirmation -- there is no second per-entry ledger that could drift
out of sync, and an interrupted run resumes from the persisted rows.

Enforced rules (LLA source governance plan):

- exactly one active source in the whole installation; the `scope_id` argument
  partitions candidate rows, it can never activate a second source;
- no platform is active until the user confirms it together with authorization
  evidence, and a failure never falls back to another platform;
- the accent must be the canonical ``en-GB``: en-US is excluded outright and any
  other accent (en-AU, mixed, unknown, arbitrary text) stays in review;
- unverified license, unverified CEFR level and unverified transcript never
  enter the formal training library, and an item whose authorization is not
  cleared is recorded *without* being downloaded;
- the catalog is discovered first (the whole list is persisted), and only then
  are entries processed in platform course order (CEFR A1 -> A2 -> B1, then
  course, then lesson order). Provider input order, audio quality and speech
  rate never re-rank it;
- discovery level is not learning eligibility: learning is capped by the CEFR
  level the existing eight-week confirmation mechanism has actually confirmed,
  never by a client-supplied stage nor by "A1 is exhausted";
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
    CONTRADICTED,
    OFFICIAL_ORIGIN,
    TRANSCRIPT_VERIFIED,
    encode_timestamped,
    transcript_agreement,
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
#: Accent canonicalisation: only British English may enter the library.
BRITISH_ACCENT = "en-GB"
US_ACCENT = "en-US"
UK_ACCENT_TOKENS = frozenset(
    {"en-gb", "en-gb-x", "en-uk", "gb", "uk", "british", "gb-en", "en-gb-scotland"}
)
#: Accents that are excluded outright (no US-English material).
EXCLUDED_ACCENTS = frozenset({"en-us", "en-us-x", "us", "american", "us-en"})
#: Accent tokens that mean "no usable evidence yet".
UNKNOWN_ACCENTS = frozenset({"", "unknown", "mixed", "n/a", "und", "na", "none", "null"})
LICENSE_CLEARED = "CLEARED"

#: Difficulty stages of the existing eight-week mechanism and the CEFR ceiling
#: each confirmed stage unlocks. The mapping is read-only here: the eight-week
#: module owns the confirmation, this service only verifies the record.
STAGES: tuple[str, ...] = ("STAGE_1", "STAGE_2", "STAGE_3")
LEARNING_LEVEL_BY_STAGE: dict[str, str] = {"STAGE_1": "A1", "STAGE_2": "A2", "STAGE_3": "B1"}

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

#: Legacy VOA program zones, kept for providers that still expose
#: `_list_episodes` (no default source uses it; it never orders ingestion).
STAGE_ZONES: dict[str, list[int]] = {
    "STAGE_1": [4456, 4791, 5254, 5535, 1581],
    "STAGE_2": [986, 1689, 955, 959, 979, 987],
    "STAGE_3": [986, 1689, 955, 959, 979, 987],
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

#: One active source for the whole installation: the key is deliberately not
#: scoped, so no scope argument can open a second active source.
_ACTIVE_KEY = "source_governance.active"
_REPORT_KEY = "source_governance.report.{cycle}"
_CONFIRM_KEY = "source_governance.confirmation"
_HISTORY_KEY = "source_governance.history"


class SourceGovernanceError(ValueError):
    """Governance refusal with a stable code (never a silent fallback)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def normalize_accent(value: object) -> str | None:
    """Canonical accent token: ``en-GB`` / ``en-US`` / the raw token / None.

    Only a British-English token maps to ``en-GB``. A US token maps to
    ``en-US`` (excluded), and any other distinct token is returned as-is so the
    caller can keep it in review instead of silently accepting it.
    """
    if value is None:
        return None
    token = re.sub(r"\s+", "", str(value)).lower().replace("_", "-")
    if token in UNKNOWN_ACCENTS:
        return None
    if token in UK_ACCENT_TOKENS:
        return BRITISH_ACCENT
    if token in EXCLUDED_ACCENTS:
        return US_ACCENT
    return token


def is_excluded_accent(value: object) -> bool:
    """True for a US-English declaration (excluded at every entry point)."""
    return normalize_accent(value) == US_ACCENT


def is_british_accent(value: object) -> bool:
    """True only for the canonical British-English accent."""
    return normalize_accent(value) == BRITISH_ACCENT


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


def is_level_allowed(cefr_level: object, ceiling: object) -> bool:
    """True when the item's CEFR level is within the confirmed learning level."""
    required = CEFR_ORDER.get(str(cefr_level or "").upper())
    allowed = CEFR_ORDER.get(str(ceiling or "").upper())
    if required is None or allowed is None:
        return False
    return required <= allowed


def assess_disposition(item: dict[str, object]) -> tuple[str, str | None]:
    """Metadata-level disposition of a catalog item.

    Returns (disposition, reason_code). `ELIGIBLE` only means the discovery
    metadata is complete; audio quality and transcript verification are still
    required before ingestion. Only the canonical en-GB accent qualifies: any
    other accent name (en-AU, mixed, unknown, arbitrary text) is review work,
    never an accepted item.
    """
    accent = normalize_accent(item.get("accent"))
    if accent == US_ACCENT:
        return REJECTED, "ACCENT_EXCLUDED_EN_US"
    if normalize_license(item.get("license_status")) != LICENSE_CLEARED:
        return PENDING_REVIEW, "LICENSE_UNVERIFIED"
    if accent is None:
        return PENDING_REVIEW, "ACCENT_UNVERIFIED"
    if accent != BRITISH_ACCENT:
        return PENDING_REVIEW, "ACCENT_NOT_UK"
    if str(item.get("cefr_level") or "").upper() not in CEFR_ORDER:
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
        "cefr_level": str(data.get("cefr_level") or "").upper() or None,
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
    """Platform course order: CEFR A1 -> A2 -> B1, then course, then lesson.

    The course *order* number decides before the course *name*: sorting by name
    first would reorder the platform's own sequence.
    """
    return (
        CEFR_ORDER.get(str(row["cefr_level"] or "").upper(), 9),
        row["course_order"] if row["course_order"] is not None else 2**31 - 1,
        row["item_order"] if row["item_order"] is not None else 2**31 - 1,
        row["source_course"] or "",
        row["provider_item_id"],
    )


def _candidate_id(platform: str, item_id: str) -> str:
    """Stable candidate identity: platform + item id, never the URL or batch."""
    digest = hashlib.sha256(f"{platform}|{item_id}".encode()).hexdigest()[:24]
    return f"cand-{digest}"


def _content_fingerprint(platform: str, item_id: str) -> str:
    return hashlib.sha256(f"{platform}|{item_id}".encode()).hexdigest()[:32]


def material_id_for(platform: str, item_id: str) -> str:
    """Formal material id: the platform is part of the identity.

    Two platforms routinely use the same lesson number, so the bare provider
    item id is not a unique material identity.
    """
    return f"{str(platform).upper()}:{item_id}"


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


def confirmed_learning_level(connection, scope_id: str) -> str:
    """CEFR learning ceiling confirmed by the existing eight-week mechanism.

    The ceiling starts at A1. It only rises when `training_difficulty_profiles`
    records a higher stage *and* the matching number of `UPGRADE_CONFIRMED`
    prompt decisions exists, so neither a stray profile row nor a client
    argument can unlock a level: the eight-week confirmation record is the
    evidence. `difficulty_progression` is not modified by this read.
    """
    stage = STAGES[0]
    try:
        row = connection.execute(
            "SELECT current_stage FROM training_difficulty_profiles WHERE scope_id = ?",
            (scope_id,),
        ).fetchone()
        if row is not None and row["current_stage"] in STAGES:
            stage = row["current_stage"]
        confirmed = connection.execute(
            """
            SELECT COUNT(*) AS n FROM upgrade_prompts
             WHERE scope_id = ? AND prompt_status = 'RESOLVED' AND decision = 'UPGRADE_CONFIRMED'
            """,
            (scope_id,),
        ).fetchone()["n"]
    except sqlite3.Error:  # a database without the difficulty tables yet
        return LEARNING_LEVEL_BY_STAGE[STAGES[0]]
    steps = min(STAGES.index(stage), int(confirmed or 0))
    return LEARNING_LEVEL_BY_STAGE[STAGES[steps]]


def active_source_platform(database: Database, scope_id: str = "default") -> str | None:
    """The active platform for the installation, if any (read-only helper)."""
    with database.connect() as connection:
        state = _read_metadata(connection, _ACTIVE_KEY)
    if not isinstance(state, dict):
        return None
    return None if state.get("platform") is None else str(state.get("platform"))


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

    def active_source(self, scope_id: str = "default", connection=None) -> dict[str, object] | None:
        with self._connection(connection) as conn:
            state = _read_metadata(conn, _ACTIVE_KEY)
        return state if isinstance(state, dict) else None

    def learning_level(self, scope_id: str = "default") -> str:
        """Recompute and record the confirmed CEFR ceiling of the active source.

        The ceiling is derived from the eight-week confirmation records and is
        persisted in the source metadata (initial A1) so the UI and the report
        show the same value the ingestion path enforces.
        """
        with self.database.connect() as connection:
            level = confirmed_learning_level(connection, scope_id)
            active = _read_metadata(connection, _ACTIVE_KEY)
            if isinstance(active, dict) and active.get("learning_level") != level:
                active["learning_level"] = level
                active["learning_level_updated_at"] = self.now_fn().isoformat()
                _write_metadata(connection, _ACTIVE_KEY, active)
        return level

    def source_state(self, scope_id: str) -> dict[str, object]:
        active = self.active_source(scope_id)
        with self._connection() as connection:
            confirmation = _read_metadata(connection, _CONFIRM_KEY)
            report = None
            if active is not None:
                cycle = int(active.get("cycle", 1))
                report = _read_metadata(connection, _REPORT_KEY.format(cycle=cycle))
        return {
            "scope_id": scope_id,
            "active_source": active,
            "next_platform": next_platform(None if active is None else str(active["platform"])),
            "available_platforms": [
                {"platform": platform, "label": SOURCE_LABELS.get(platform, platform)}
                for platform in SOURCE_ORDER
            ],
            "learning_level": None if active is None else active.get("learning_level"),
            "last_report": None if report is None else _report_summary(report),
            "confirmation": confirmation,
            "discovery": None if active is None else _catalog_summary(active),
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
        """Activate the first source cycle.

        Requires an explicitly evaluated platform plus authorization and
        catalog-scope evidence. The active source is installation-wide: a
        second platform can only be activated through the confirmed switch
        flow on top of a current report, and no scope argument can bypass it.
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
                "scope_id": scope_id,
                "license_reference": str(license_reference),
                "catalog_scope": str(catalog_scope),
                "activated_at": now.isoformat(),
                "confirmed_by": confirmed_by,
                "note": note,
                "previous_platform": None,
                # The learning ceiling starts at A1; it only rises through the
                # eight-week confirmation records (see learning_level()).
                "learning_level": confirmed_learning_level(connection, scope_id),
                "catalog": None,
            }
            _write_metadata(connection, _ACTIVE_KEY, state)
            history = _read_metadata(connection, _HISTORY_KEY) or []
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
            _write_metadata(connection, _HISTORY_KEY, history[-20:])
        return state

    def report(self, scope_id: str) -> dict[str, object]:
        """Generate and persist the completion report for the current cycle.

        The report is recomputed from the persisted candidate rows and the
        recorded catalog snapshot at call time, so it always reflects the
        current list. Coverage, classification, legal acquisition, ingestion
        and the remaining items are reported separately: discovering the whole
        catalog never means legally acquiring or ingesting all of it.
        """
        with self.database.connect() as connection:
            return self._write_report(connection, scope_id)

    def _write_report(self, connection, scope_id: str) -> dict[str, object]:
        active = _read_metadata(connection, _ACTIVE_KEY)
        if not isinstance(active, dict):
            raise SourceGovernanceError("SOURCE_NOT_APPROVED", "no active source to report on")
        report = _build_report(connection, scope_id, active, self.now_fn().isoformat())
        _write_metadata(connection, _REPORT_KEY.format(cycle=int(active.get("cycle", 1))), report)
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

        The confirmation is bound to one report version *and* the report is
        recomputed inside this write transaction: any discovery, review,
        ingestion or catalog change since the report was generated invalidates
        the old confirmation, and an old report_id can never be replayed.
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
            stored = _read_metadata(connection, _REPORT_KEY.format(cycle=cycle))
            if not isinstance(stored, dict) or stored.get("report_id") != report_id:
                raise SourceGovernanceError(
                    "CONFIRMATION_STALE",
                    "the confirmed report is missing or has been regenerated",
                )
            # Recompute from the live rows: a report that no longer describes
            # the current discovery/review/ingestion state is stale.
            live = _build_report(connection, scope_id, active, now.isoformat())
            if live["state_fingerprint"] != stored.get("state_fingerprint"):
                raise SourceGovernanceError(
                    "CONFIRMATION_STALE",
                    "the discovery/review/ingestion state changed after the report",
                )
            if not live["catalog_complete"] or live["has_cursor"]:
                raise SourceGovernanceError(
                    "SOURCE_CATALOG_INCOMPLETE",
                    "the catalog snapshot is incomplete; completion cannot be reported",
                )
            if live["known_total"] is None:
                raise SourceGovernanceError(
                    "SOURCE_CATALOG_INCOMPLETE",
                    "the catalog total is unknown; completion cannot be reported",
                )
            if live["discovered"] != live["known_total"]:
                raise SourceGovernanceError(
                    "SOURCE_CATALOG_INCOMPLETE",
                    f"only {live['discovered']} of {live['known_total']} catalog entries are discovered",
                )
            if (
                live["retryable_failures"]
                or live["qualified_unprocessed"]
                or live["pending_processing"]
            ):
                raise SourceGovernanceError(
                    "SOURCE_ITEMS_PENDING",
                    "retryable failures or unprocessed qualified items remain",
                )
            decision_items = list(live["pending_manual_items"]) + list(live["deferred_items"])
            if decision_items and not acknowledge_pending:
                raise SourceGovernanceError(
                    "PENDING_ITEMS_REQUIRE_DECISION",
                    "items awaiting manual confirmation or a confirmed learning level "
                    "must be explicitly acknowledged",
                )
            state = {
                "platform": platform,
                "cycle": cycle + 1,
                "scope_id": scope_id,
                "license_reference": str(license_reference),
                "catalog_scope": str(catalog_scope),
                "activated_at": now.isoformat(),
                "confirmed_by": confirmed_by,
                "note": None,
                "previous_platform": current_platform,
                "previous_cycle": cycle,
                "confirmed_report_id": report_id,
                "learning_level": confirmed_learning_level(connection, scope_id),
                "catalog": None,
            }
            _write_metadata(connection, _ACTIVE_KEY, state)
            _write_metadata(
                connection,
                _CONFIRM_KEY,
                {
                    "report_id": report_id,
                    "from_platform": current_platform,
                    "to_platform": platform,
                    "cycle": cycle + 1,
                    "confirmed_at": now.isoformat(),
                    "confirmed_by": confirmed_by,
                    "acknowledged_pending_items": report_pending(live),
                },
            )
            history = _read_metadata(connection, _HISTORY_KEY) or []
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
            _write_metadata(connection, _HISTORY_KEY, history[-20:])
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

        The complete catalog list is persisted first, and only then are entries
        processed (download, quality, transcription) in platform course order:
        the provider's input order, the audio grade and the client-supplied
        stage never decide what comes next. There is no 15-20 minute gate: an
        explicit band is honoured when the caller passes one, otherwise every
        original lesson length is allowed and the existing audio-quality bar
        still applies.
        """
        if speed_stage not in STAGES:
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
        declared_catalog = self._catalog_info(provider)
        ceiling = self.learning_level(scope_id)
        work_dir = self.settings.processed_dir / "p1-candidates" / batch_id
        work_dir.mkdir(parents=True, exist_ok=True)

        items = [
            normalize_item(entry)
            for entry in self._provider_items(provider, speed_stage)[:PROCESS_LIMIT]
        ]

        # 1. Persist the whole discovered list (no download happens here).
        with self.database.connect() as connection:
            # A concurrent confirmed switch must not have its catalog snapshot
            # overwritten by this (now stale) cycle.
            current = _read_metadata(connection, _ACTIVE_KEY)
            if (
                not isinstance(current, dict)
                or str(current.get("platform")) != platform
                or int(current.get("cycle", 1)) != cycle
            ):
                raise SourceGovernanceError(
                    "SOURCE_CYCLE_CONFLICT",
                    "the active source changed while the catalog was being discovered",
                )
            catalog = _catalog_snapshot(declared_catalog, current, items)
            for item in items:
                disposition, reason = assess_disposition(item)
                duration = item.get("duration_seconds")
                if disposition in (ELIGIBLE, PENDING_REVIEW):
                    if duration is None:
                        rejected["duration_missing"] = rejected.get("duration_missing", 0) + 1
                        disposition, reason = OUT_OF_SCOPE, "DURATION_MISSING"
                    elif not _duration_in_band(float(duration), lower, upper):
                        rejected["duration_out_of_range"] = (
                            rejected.get("duration_out_of_range", 0) + 1
                        )
                        disposition, reason = OUT_OF_SCOPE, "DURATION_OUT_OF_SCOPE"
                if disposition == REJECTED:
                    key = "accent_excluded" if reason == "ACCENT_EXCLUDED_EN_US" else "quarantined"
                    rejected[key] = rejected.get(key, 0) + 1
                if disposition == ELIGIBLE:
                    # Discovery metadata is complete but the entry is not
                    # processed yet (no audio, quality or transcript evidence):
                    # it waits as PENDING, never as an ingestible candidate.
                    disposition, reason = PENDING, None
                inserted = self._persist_discovery(
                    connection,
                    scope_id=scope_id,
                    platform=platform,
                    item=item,
                    disposition=disposition,
                    reason=reason,
                    batch_id=batch_id,
                    speed_stage=speed_stage,
                    now=now,
                )
                if not inserted:
                    rejected["duplicate"] = rejected.get("duplicate", 0) + 1
            current["catalog"] = catalog
            current["catalog_updated_at"] = now.isoformat()
            current["learning_level"] = ceiling
            _write_metadata(connection, _ACTIVE_KEY, current)

        # 2. Process entries in course order, within the confirmed learning level.
        processed = 0
        for row in self._processable(scope_id, platform, ceiling, now):
            if processed >= max_results:
                break
            outcome, key = self._process_row(
                provider,
                row,
                scope_id=scope_id,
                platform=platform,
                batch_id=batch_id,
                now=now,
                speed_stage=speed_stage,
                lower=lower,
                upper=upper,
                work_dir=work_dir,
            )
            if key:
                rejected[key] = rejected.get(key, 0) + 1
            if outcome == ELIGIBLE:
                processed += 1

        self._write_search_audit(
            batch_id, scope_id, speed_stage, self._current_rows(scope_id, platform, ELIGIBLE),
            rejected, now, platform,
        )
        candidates = self._rows_in_course_order(scope_id, platform, ELIGIBLE)
        return {
            "search_batch_id": batch_id,
            "scope_id": scope_id,
            "speed_stage": speed_stage,
            "active_source": {
                "platform": platform,
                "cycle": cycle,
                "learning_level": ceiling,
            },
            "candidates": [_payload(row, platform) for row in candidates[:max_results]],
            "deferred_by_level": [
                _payload(row, platform)
                for row in self._rows_in_course_order(scope_id, platform, PENDING)
                if not is_level_allowed(row["cefr_level"], ceiling)
            ],
            "pending_review": [
                _payload(row, platform)
                for row in self._rows_in_course_order(scope_id, platform, PENDING_REVIEW)
            ],
            "quarantined": [
                _payload(row, platform)
                for row in self._rows_in_course_order(scope_id, platform, REJECTED)
            ],
            "rejection_summary": rejected,
            "catalog": {
                "catalog_complete": bool(catalog.get("catalog_complete")),
                "catalog_scope": catalog.get("catalog_scope"),
                "known_total": catalog.get("known_total"),
                "has_cursor": bool(catalog.get("has_cursor")),
                "discovered": catalog.get("discovered"),
            },
            "next_qualified_candidate_id": next(
                (row["candidate_id"] for row in candidates), None
            ),
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

        Approval requires the canonical en-GB accent, cleared license, known
        CEFR level and verified transcript evidence; without them the item
        stays out of the formal training library.
        """
        decision = str(decision).strip().upper()
        if decision not in ("APPROVE", "REJECT"):
            raise ValueError("decision must be APPROVE or REJECT")
        active = self.active_source(scope_id)
        if active is None:
            raise SourceGovernanceError("SOURCE_NOT_APPROVED", "no active source to review against")
        platform = str(active["platform"])
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
            if candidate["ingestion_status"] == INGESTED:
                raise SourceGovernanceError(
                    "CANDIDATE_ALREADY_INGESTED", "an ingested candidate cannot be re-reviewed"
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
                if confirmed_accent != BRITISH_ACCENT:
                    raise SourceGovernanceError(
                        "ACCENT_NOT_ELIGIBLE",
                        "approval requires the canonical en-GB accent with evidence",
                    )
                if normalize_license(candidate["license_status"]) != LICENSE_CLEARED:
                    raise SourceGovernanceError(
                        "LICENSE_UNVERIFIED", "approval requires cleared source license"
                    )
                if str(candidate["cefr_level"] or "").upper() not in CEFR_ORDER:
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
                if not candidate["audio_path"]:
                    # Nothing was downloaded (authorization or catalog gate):
                    # the item stays processable instead of becoming a formal
                    # material without audio.
                    raise SourceGovernanceError(
                        "AUDIO_NOT_ACQUIRED",
                        "approval requires the original audio to be legally acquired",
                    )
                status, reason = ELIGIBLE, None
                updates.update(
                    {
                        "accent": BRITISH_ACCENT,
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
        return {
            "candidate_id": candidate_id,
            "ingestion_status": status,
            "reason_code": reason,
            "reviewed_by": reviewer,
            "reviewed_at": now.isoformat(),
            "note": note,
        }

    def next_qualified(self, scope_id: str, connection=None) -> dict[str, object] | None:
        """The single next ingestible item, in platform course order.

        Only a processed, verified item within the confirmed CEFR learning
        level qualifies; discovery can cross levels, ingestion cannot.
        """
        with self._connection(connection) as conn:
            active = self.active_source(scope_id, connection=conn)
            if active is None:
                return None
            ceiling = confirmed_learning_level(conn, scope_id)
            rows = conn.execute(
                """
                SELECT * FROM material_candidates
                 WHERE scope_id = ? AND provider = ? AND ingestion_status = ?
                   AND candidate_status IN ('CANDIDATE', 'SELECTED')
                   AND audio_path IS NOT NULL
                """,
                (scope_id, str(active["platform"]), ELIGIBLE),
            ).fetchall()
        now = self.now_fn()
        usable = [
            row
            for row in rows
            if (expiry := _parse_timestamp(row["expires_at"])) is None or expiry >= now
        ]
        usable = [row for row in usable if is_level_allowed(row["cefr_level"], ceiling)]
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

    def _persist_discovery(
        self,
        connection,
        *,
        scope_id: str,
        platform: str,
        item: dict[str, object],
        disposition: str,
        reason: str | None,
        batch_id: str,
        speed_stage: str,
        now: datetime,
    ) -> bool:
        """Record one discovered entry; never overwrite a progressed row.

        The row is the entry's only state record, so a re-run after a crash
        keeps everything already learned about it (including a review or an
        ingestion decision) instead of replacing it with fresh metadata.
        """
        item_id = str(item["source_item_id"])
        existing = connection.execute(
            "SELECT candidate_id FROM material_candidates WHERE provider = ? AND provider_item_id = ?",
            (platform, item_id),
        ).fetchone()
        if existing is not None:
            return False
        expires_at = now + timedelta(days=CANDIDATE_TTL_DAYS)
        try:
            connection.execute(
                """
                INSERT INTO material_candidates(
                    candidate_id, scope_id, provider, provider_item_id, title, source_url,
                    audio_url, transcript, duration_seconds, speed_stage, audio_quality,
                    transcript_status, candidate_status, search_batch_id,
                    content_fingerprint, created_at, expires_at,
                    source_course, course_order, item_order, accent, speaker_id, cefr_level,
                    transcript_verified, license_status, license_reference, ingestion_status,
                    source_priority, transcript_origin, accent_evidence, transcript_evidence,
                    failure_code
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, NULL,
                    NULL, 'CANDIDATE', ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?
                )
                """,
                (
                    _candidate_id(platform, item_id), scope_id, platform, item_id,
                    str(item["title"]), str(item["source_url"]),
                    str(item["audio_url"]), item.get("transcript"),
                    item.get("duration_seconds"), speed_stage,
                    batch_id, _content_fingerprint(platform, item_id),
                    now.isoformat(), expires_at.isoformat(),
                    item.get("source_course"), item.get("course_order"), item.get("item_order"),
                    item.get("accent"), item.get("speaker_id"), item.get("cefr_level"),
                    1 if item.get("transcript_verified") else 0,
                    normalize_license(item.get("license_status")), item.get("license_reference"),
                    disposition,
                    _platform_priority(platform), item.get("transcript_origin"),
                    item.get("accent_evidence"), item.get("transcript_evidence"),
                    reason,
                ),
            )
        except sqlite3.IntegrityError:
            # A concurrent discovery of the same identity already landed; the
            # first record is the single source of truth.
            return False
        return True

    def _processable(
        self, scope_id: str, platform: str, ceiling: str, now: datetime
    ) -> list[dict[str, object]]:
        """Entries due for processing, in platform course order.

        Only entries whose authorization is cleared are downloaded (an item
        awaiting authorization is recorded without any download), and only
        entries within the confirmed learning level are processed at all.
        """
        with self.database.connect() as connection:
            # An expired but otherwise qualified entry is retried instead of
            # being skipped forever: its audio and evidence are re-validated
            # (the declared transcript is source metadata and is kept).
            connection.execute(
                """
                UPDATE material_candidates
                   SET ingestion_status = ?, audio_path = NULL,
                       transcript_status = NULL, audio_quality = NULL,
                       timestamped_sentences_json = NULL
                 WHERE scope_id = ? AND provider = ? AND ingestion_status = ?
                   AND expires_at IS NOT NULL AND expires_at < ?
                """,
                (PENDING, scope_id, platform, ELIGIBLE, now.isoformat()),
            )
            rows = connection.execute(
                """
                SELECT * FROM material_candidates
                 WHERE scope_id = ? AND provider = ?
                   AND (
                        ingestion_status = ?
                        OR (ingestion_status IN (?, ?) AND audio_path IS NULL)
                   )
                """,
                (scope_id, platform, RETRYABLE_FAILED, PENDING, PENDING_REVIEW),
            ).fetchall()
        due = []
        for row in rows:
            if normalize_license(row["license_status"]) != LICENSE_CLEARED:
                continue
            if normalize_accent(row["accent"]) == US_ACCENT:
                continue
            if not is_level_allowed(row["cefr_level"], ceiling):
                continue
            if str(row["cefr_level"] or "").upper() not in CEFR_ORDER:
                continue
            due.append(dict(row))
        due.sort(key=_course_key)
        return due

    def _process_row(
        self,
        provider,
        row: dict[str, object],
        *,
        scope_id: str,
        platform: str,
        batch_id: str,
        now: datetime,
        speed_stage: str,
        lower: float | None,
        upper: float | None,
        work_dir: Path,
    ) -> tuple[str | None, str | None]:
        """Download, grade and transcribe one entry; persist the outcome."""
        item_id = str(row["provider_item_id"])
        item = {
            "source_item_id": item_id,
            "source_url": row["source_url"],
            "audio_url": row["audio_url"],
            "transcript": row["transcript"],
            "transcript_origin": row["transcript_origin"],
            "transcript_verified": bool(row["transcript_verified"]),
            "accent": row["accent"],
            "cefr_level": row["cefr_level"],
            "license_status": row["license_status"],
            "title": row["title"],
            "source_course": row["source_course"],
            "course_order": row["course_order"],
            "item_order": row["item_order"],
        }
        prior_reason = row["failure_code"]
        metadata_disposition, metadata_reason = assess_disposition(item)
        try:
            audio_path = self._fetch_audio(provider, item, work_dir)
        except Exception:
            self._update_processed(
                row["candidate_id"], RETRYABLE_FAILED, "DOWNLOAD_FAILED", audio_path=None
            )
            return None, "download_failed"

        quality = self._analyze_audio(audio_path)
        if quality is None:
            self._update_processed(
                row["candidate_id"], RETRYABLE_FAILED, "AUDIO_UNREADABLE", audio_path=str(audio_path)
            )
            return None, "quality_failed"
        if quality.level == "Poor":
            self._audio_report(row["candidate_id"], quality, now)
            self._update_processed(
                row["candidate_id"], REJECTED, "QUALITY_POOR",
                audio_path=str(audio_path), quality=quality,
            )
            return REJECTED, "quality_poor"
        if not _duration_in_band(quality.duration_seconds, lower, upper):
            self._audio_report(row["candidate_id"], quality, now)
            self._update_processed(
                row["candidate_id"], OUT_OF_SCOPE, "DURATION_OUT_OF_SCOPE",
                audio_path=str(audio_path), quality=quality,
            )
            return OUT_OF_SCOPE, "duration_out_of_range"

        try:
            segments = self._retry(lambda: self.asr.transcribe(str(audio_path)))
        except Exception:
            self._audio_report(row["candidate_id"], quality, now)
            self._update_processed(
                row["candidate_id"], RETRYABLE_FAILED, "ASR_FAILED",
                audio_path=str(audio_path), quality=quality,
            )
            return None, "transcribe_failed"

        official = str(item.get("transcript") or "").strip()
        asr_text = " ".join(segment.text for segment in segments).strip()
        if str(item.get("transcript_origin") or "").upper() == OFFICIAL_ORIGIN and official:
            transcript = official
            agreement = transcript_agreement(official, asr_text)
        else:
            transcript = asr_text
            agreement = None
        sentences = split_sentences(transcript) if transcript else []
        aligned = align_sentences(sentences, segments) if sentences else []
        validation = validate_transcript(
            transcript, aligned, quality.duration_seconds, MIN_COVERAGE, MIN_SENTENCES
        )
        if validation.status != "COMPLETE":
            self._audio_report(row["candidate_id"], quality, now)
            self._update_processed(
                row["candidate_id"], REJECTED, f"TRANSCRIPT_{validation.status}",
                audio_path=str(audio_path), quality=quality,
            )
            return REJECTED, f"transcript_{validation.status.lower()}"

        verification = transcript_verification(
            validation,
            item.get("transcript_origin"),
            bool(item.get("transcript_verified")),
            agreement,
        )
        if verification == TRANSCRIPT_VERIFIED and metadata_disposition == ELIGIBLE:
            final_disposition, final_reason = ELIGIBLE, None
        else:
            final_disposition = PENDING_REVIEW
            if verification == CONTRADICTED:
                final_reason = "TRANSCRIPT_CONTRADICTED"
            else:
                final_reason = metadata_reason or prior_reason or "TRANSCRIPT_UNVERIFIED"
        self._audio_report(row["candidate_id"], quality, now)
        self._update_processed(
            row["candidate_id"], final_disposition, final_reason,
            audio_path=str(audio_path), quality=quality, transcript=transcript,
            aligned=aligned, transcript_status=validation.status,
            expires_at=now + timedelta(days=CANDIDATE_TTL_DAYS),
        )
        return final_disposition, None

    def _update_processed(
        self,
        candidate_id: str,
        disposition: str,
        reason: str | None,
        *,
        audio_path: str | None = None,
        quality=None,
        transcript: str | None = None,
        aligned: list[tuple[str, float, float]] | None = None,
        transcript_status: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "ingestion_status": disposition,
            "failure_code": reason,
            "audio_path": audio_path,
        }
        if quality is not None:
            fields["audio_quality"] = quality.level
            fields["duration_seconds"] = quality.duration_seconds
        if transcript is not None:
            fields["transcript"] = transcript
        if aligned is not None:
            fields["timestamped_sentences_json"] = encode_timestamped(aligned)
        if transcript_status is not None:
            fields["transcript_status"] = transcript_status
        if expires_at is not None:
            fields["expires_at"] = expires_at.isoformat()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with self.database.connect() as connection:
            connection.execute(
                f"""
                UPDATE material_candidates
                   SET {assignments}, attempt_count = attempt_count + 1
                 WHERE candidate_id = ?
                """,
                (*fields.values(), candidate_id),
            )

    def _audio_report(self, candidate_id: str, quality, now: datetime) -> None:
        """Persist the quality evidence for one processed candidate."""
        report_id = str(uuid4())
        with self.database.connect() as connection:
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
            connection.execute(
                "UPDATE material_candidates SET audio_quality_report_id = ? WHERE candidate_id = ?",
                (report_id, candidate_id),
            )

    def _current_rows(self, scope_id: str, platform: str, disposition: str) -> list:
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM material_candidates
                 WHERE scope_id = ? AND provider = ? AND ingestion_status = ?
                """,
                (scope_id, platform, disposition),
            ).fetchall()

    def _rows_in_course_order(
        self, scope_id: str, platform: str, disposition: str
    ) -> list[dict[str, object]]:
        rows = [dict(row) for row in self._current_rows(scope_id, platform, disposition)]
        rows.sort(key=_course_key)
        return rows

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

    def _write_search_audit(
        self,
        batch_id: str,
        scope_id: str,
        speed_stage: str,
        candidates: list,
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


def _catalog_snapshot(declared: dict[str, object] | None, active: dict, items: list) -> dict:
    """Catalog evidence for the current cycle.

    A declared snapshot is only accepted as evidence of the *catalog scope*;
    the number of entries actually discovered is always derived from the
    persisted list, and an undeclared snapshot never claims completion.
    """
    if declared is None:
        return {
            "catalog_complete": False,
            "known_total": None,
            "has_cursor": True,
            "catalog_scope": active.get("catalog_scope"),
            "discovered": len(items),
        }
    return {
        "catalog_complete": bool(declared.get("catalog_complete")),
        "known_total": declared.get("known_total"),
        "has_cursor": bool(declared.get("has_cursor", False)),
        "catalog_scope": declared.get("catalog_scope") or active.get("catalog_scope"),
        "discovered": len(items),
    }


def _payload(row, platform: str) -> dict[str, object]:
    """Public view of one candidate (metadata only, never the transcript)."""
    duration = row["duration_seconds"]
    transcript = row["transcript"]
    wpm = None
    if duration and transcript:
        words = len(str(transcript).split())
        wpm = round(words / (float(duration) / 60.0), 1) if duration else None
    return {
        "candidate_id": row["candidate_id"],
        "provider": row["provider"] or platform,
        "provider_item_id": row["provider_item_id"],
        "title": row["title"],
        "duration_seconds": None if duration is None else round(float(duration), 1),
        "speed_stage": row["speed_stage"],
        "audio_quality": row["audio_quality"],
        "transcript_status": row["transcript_status"],
        "candidate_status": row["candidate_status"],
        "speech_rate_wpm": wpm,
        "cefr_level": row["cefr_level"],
        "accent": row["accent"],
        "source_course": row["source_course"],
        "item_order": row["item_order"],
        "ingestion_status": row["ingestion_status"],
        "reason_code": row["failure_code"],
    }


def _build_report(connection, scope_id: str, active: dict, generated_at: str) -> dict[str, object]:
    """Report the current persisted state; nothing is read from a side ledger."""
    platform = str(active["platform"])
    cycle = int(active.get("cycle", 1))
    catalog = active.get("catalog") or {}
    ceiling = confirmed_learning_level(connection, scope_id)
    rows = connection.execute(
        """
        SELECT provider_item_id, ingestion_status, failure_code, cefr_level, audio_path
          FROM material_candidates
         WHERE scope_id = ? AND provider = ?
         ORDER BY provider_item_id
        """,
        (scope_id, platform),
    ).fetchall()
    dispositions: dict[str, int] = {}
    downloaded = 0
    pending_manual: list[str] = []
    retryable: list[str] = []
    qualified_unprocessed: list[str] = []
    pending_processing: list[str] = []
    deferred: list[str] = []
    for row in rows:
        disposition = str(row["ingestion_status"])
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        if row["audio_path"]:
            downloaded += 1
        item_id = str(row["provider_item_id"])
        if disposition == PENDING_REVIEW:
            pending_manual.append(item_id)
        elif disposition == RETRYABLE_FAILED:
            retryable.append(item_id)
        elif disposition == ELIGIBLE:
            qualified_unprocessed.append(item_id)
        elif disposition == PENDING:
            if is_level_allowed(row["cefr_level"], ceiling):
                pending_processing.append(item_id)
            else:
                deferred.append(item_id)
    discovered = len(rows)
    known_total = catalog.get("known_total")
    catalog_complete = bool(catalog.get("catalog_complete"))
    has_cursor = bool(catalog.get("has_cursor"))
    blocking = retryable + qualified_unprocessed + pending_processing
    if not catalog_complete or has_cursor:
        completion_status = "INCOMPLETE"
    elif known_total is None:
        completion_status = "UNKNOWN_TOTAL"
    elif discovered != int(known_total) or blocking:
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
        "catalog_scope": catalog.get("catalog_scope"),
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
        "pending_processing": pending_processing,
        "deferred_items": deferred,
        "beyond_learning_level": deferred,
        "learning_level": ceiling,
        "completion_status": completion_status,
        "state_fingerprint": _state_fingerprint(rows, catalog, ceiling),
    }


def _state_fingerprint(rows, catalog: dict, ceiling: str) -> str:
    """Content hash of the current discovery/review/ingestion state.

    Used to bind a confirmation to the exact state the report described: any
    new discovery, review decision, ingestion or catalog change moves the hash,
    so an old report (and its confirmation) becomes stale.
    """
    payload = {
        "items": [
            [
                row["provider_item_id"],
                row["ingestion_status"],
                row["failure_code"],
                row["cefr_level"],
                1 if row["audio_path"] else 0,
            ]
            for row in rows
        ],
        "catalog": {
            "catalog_complete": bool(catalog.get("catalog_complete")),
            "known_total": catalog.get("known_total"),
            "has_cursor": bool(catalog.get("has_cursor")),
            "catalog_scope": catalog.get("catalog_scope"),
        },
        "learning_level": ceiling,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def report_pending(report: dict[str, object]) -> list[str]:
    """Items a switch decision acknowledges (manual review + level deferral)."""
    return [*report.get("pending_manual_items", []), *report.get("deferred_items", [])]


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
        "beyond_learning_level",
        "learning_level",
    )
    return {key: report.get(key) for key in keys}


def _catalog_summary(active: dict) -> dict[str, object]:
    catalog = active.get("catalog") or {}
    return {
        "catalog_complete": bool(catalog.get("catalog_complete")),
        "has_cursor": bool(catalog.get("has_cursor")),
        "known_total": catalog.get("known_total"),
        "catalog_scope": catalog.get("catalog_scope"),
        "discovered": catalog.get("discovered"),
        "updated_at": active.get("catalog_updated_at"),
        "learning_level": active.get("learning_level"),
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
            "noise_floor_measured": bool(quality.noise_floor_measured),
        },
        sort_keys=True,
    )
