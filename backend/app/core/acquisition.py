"""Governed network acquisition: one active source, official artifacts only.

What this module will and will not do
-------------------------------------
The three candidate platforms were assessed (docs/LLA_Primary_Source_Authorization_Assessment.md).
All three forbid *scraping*; two also block automated requests at the edge. The
specification for this feature says explicitly: "如果网站不允许自动获取,不得绕过访问
限制。应记录阻塞原因并评估下一候选来源。" So this module never scrapes lesson
pages and never retries a blocked endpoint with a disguise.

What it *does* automate is the one artifact the publisher itself offers as a
download: the official English (UK) audio pack (``ENEN.ZIP``), whose audio is
CC BY-NC-ND 3.0 US. Fetching one offered file is not crawling; the pack is then
unpacked into the manual import inbox, where the existing governed path takes
over (quarantine -> human approval -> pool).

Text stays a human/ASR step: letter-perfect dictation needs a trustworthy key,
and the lesson pages are not machine-readable for us.

Everything else is policy, not code: :data:`SOURCES` records, per platform, what
may be automated and what is blocked, with the reason. ``assert_can_automate``
refuses anything that is not explicitly allowed.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

USER_AGENT = "LLA-local-acquisition/1.0 (personal single-user study tool)"
HTTP_TIMEOUT = 120


class AcquisitionError(RuntimeError):
    """Raised when acquisition must not proceed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourcePolicy:
    """Per-platform automation policy, derived from the authorization review."""

    platform: str
    label: str
    audio_license: str
    text_license: str
    audio_mode: str  # "official-pack" (allowed) | "blocked" | "prohibited"
    text_mode: str  # "manual" | "blocked" | "prohibited"
    blocker: str | None
    artifact_url: str | None = None


SOURCES: tuple[SourcePolicy, ...] = (
    SourcePolicy(
        platform="50LANGUAGES",
        label="50languages / book2 (English UK)",
        audio_license="CC BY-NC-ND 3.0 US (audio only)",
        text_license="All rights reserved (no licence)",
        audio_mode="official-pack",
        text_mode="manual",
        blocker=(
            "lesson pages are only intermittently reachable (Cloudflare challenge, HTTP 403 "
            "on plain GET) and Terms of Use section 6 forbids bulk automated retrieval, so "
            "text is never harvested; only the single official audio pack the publisher "
            "offers is fetched"
        ),
        artifact_url="https://www.50idiomas.com/_1language/ENEN.ZIP",
    ),
    SourcePolicy(
        platform="BRITISH_COUNCIL",
        label="British Council LearnEnglish",
        audio_license="personal, non-commercial use with credit",
        text_license="personal, non-commercial use with credit",
        audio_mode="blocked",
        text_mode="blocked",
        blocker=(
            "Terms of Use forbid modifying content 'in any way' and copying it into products; "
            "every automated request is refused by the edge (HTTP 403/Akamai)"
        ),
    ),
    SourcePolicy(
        platform="BREAKING_NEWS_ENGLISH",
        label="Breaking News English (British English)",
        audio_license="written permission required",
        text_license="written permission required",
        audio_mode="prohibited",
        text_mode="prohibited",
        blocker=(
            "copyright page names 'app, LMS or CMS' explicitly and permits only printed copies; "
            "linking to mp3 files is excluded, so nothing may be automated"
        ),
    ),
)

_BY_PLATFORM = {source.platform: source for source in SOURCES}


def source_policy(platform: str) -> SourcePolicy:
    policy = _BY_PLATFORM.get(platform.upper())
    if policy is None:
        raise AcquisitionError("UNKNOWN_SOURCE", f"no acquisition policy for platform {platform!r}")
    return policy


def assert_can_automate(platform: str) -> SourcePolicy:
    """Refuse any platform whose audio may not be fetched automatically."""
    policy = source_policy(platform)
    if policy.audio_mode != "official-pack" or not policy.artifact_url:
        raise AcquisitionError(
            "SOURCE_BLOCKED_BY_POLICY",
            f"{policy.platform} may not be acquired automatically: {policy.blocker}",
        )
    return policy


# --------------------------------------------------------------------------- #
# live probing (real network, no content download)
# --------------------------------------------------------------------------- #
def _head(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "status": response.status,
                "length": response.headers.get("Content-Length"),
                "content_type": response.headers.get("Content-Type"),
            }
    except urllib.error.HTTPError as error:
        return {"status": error.code, "error": str(error)}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"status": None, "error": str(error)}


def probe_source(policy: SourcePolicy) -> dict[str, Any]:
    """Probe only what the policy allows to be acquired.

    Blocked or manually obtained sources are not touched at all: no request is
    sent to a page we may not use, and the report carries the policy record
    instead. Whether a forbidden page happens to answer is not knowledge this
    tool should even collect.
    """
    report: dict[str, Any] = {
        "platform": policy.platform,
        "audio_mode": policy.audio_mode,
        "text_mode": policy.text_mode,
        "blocker": policy.blocker,
        "probed": False,
    }
    if policy.audio_mode == "official-pack" and policy.artifact_url:
        report["artifact"] = {"url": policy.artifact_url, **_head(policy.artifact_url)}
        report["probed"] = True
    else:
        report["skipped"] = "policy marks this source as blocked or manual; no request sent"
    return report


def probe_all() -> list[dict[str, Any]]:
    return [probe_source(policy) for policy in SOURCES]


def first_automation_allowed() -> SourcePolicy:
    """The first platform in priority order whose audio may be automated."""
    for policy in SOURCES:
        if policy.audio_mode == "official-pack" and policy.artifact_url:
            return policy
    raise AcquisitionError("NO_USABLE_SOURCE", "no candidate platform permits automated acquisition")


# --------------------------------------------------------------------------- #
# reserve planning (pure)
# --------------------------------------------------------------------------- #
def reserve_plan(ready_materials: int, per_day: float = 1.0, days: int = 7) -> dict[str, Any]:
    """How much material a ``days``-long reserve needs, and whether to top up.

    Deliberately simple: one material per day by default, a plain shortfall
    calculation, no scheduler. Existing configuration wins over this default.
    """
    if per_day < 0 or days < 0:
        raise AcquisitionError("INVALID_PLAN", "per_day and days must not be negative")
    needed = int(math.ceil(per_day * days)) if per_day and days else 0
    shortfall = max(0, needed - max(0, int(ready_materials)))
    return {
        "per_day": per_day,
        "days": days,
        "needed": needed,
        "ready": max(0, int(ready_materials)),
        "shortfall": shortfall,
        "should_topup": shortfall > 0,
    }


# --------------------------------------------------------------------------- #
# official pack fetch (single offered artifact)
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_is_valid(path: Path) -> bool:
    """A cache file is usable only when every member passes its CRC check."""
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def require_active_source(database: Any, platform: str, scope_id: str = "default") -> str:
    """Acquisition is only allowed for the platform that is currently active."""
    from app.core.material_candidates import active_source_platform

    active = active_source_platform(database, scope_id)
    if active is None:
        raise AcquisitionError(
            "SOURCE_NOT_APPROVED",
            "no active material source; activate a source before acquiring anything",
        )
    if str(active).upper() != platform.upper():
        raise AcquisitionError(
            "SOURCE_MISMATCH",
            f"active source is {active}, so {platform} may not be acquired",
        )
    return str(active)


def _download(url: str, target: Path) -> dict[str, Any]:
    """Stream one file into a ``.part`` path; a partial file is never kept."""
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response, target.open("wb") as handle:
        expected = response.headers.get("Content-Length")
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            received += len(block)
            digest.update(block)
            handle.write(block)
    if expected is not None and int(expected) != received:
        target.unlink(missing_ok=True)
        raise AcquisitionError(
            "INCOMPLETE_DOWNLOAD",
            f"expected {expected} bytes but received {received}; the partial file was discarded so a retry is a plain retry",
        )
    return {"url": url, "bytes": received, "sha256": digest.hexdigest()}


def fetch_official_pack(
    database: Any,
    platform: str,
    inbox_root: Path,
    *,
    batch: str = "book2-en-uk",
    force: bool = False,
    scope_id: str = "default",
) -> dict[str, Any]:
    """Fetch the publisher's own audio pack, but only for the active source.

    Check order is deliberate: policy first (never automate what is forbidden),
    then the active source (never acquire material for a platform that is not in
    use). The archive streams into a ``.part`` file, must pass its CRC check, and
    only then is promoted into the cache - so an interrupted download can never
    be mistaken for a cache hit, and retrying is just running the command again.
    """
    policy = assert_can_automate(platform)
    assert policy.artifact_url is not None
    require_active_source(database, policy.platform, scope_id)

    name = policy.artifact_url.rsplit("/", 1)[-1]
    cache = inbox_root.parent / "cache" / name
    part = cache.with_suffix(cache.suffix + ".part")
    batch_dir = inbox_root / batch

    if not force and cache.is_file() and archive_is_valid(cache):
        download: dict[str, Any] = {
            "url": policy.artifact_url,
            "path": str(cache),
            "bytes": cache.stat().st_size,
            "sha256": sha256_file(cache),
            "cached": True,
        }
    else:
        part.unlink(missing_ok=True)
        download = _download(policy.artifact_url, part)
        if not archive_is_valid(part):
            part.unlink(missing_ok=True)
            raise AcquisitionError(
                "INCOMPLETE_DOWNLOAD",
                "downloaded archive failed its CRC check and was discarded; run the command again to retry",
            )
        part.replace(cache)
        download["path"] = str(cache)

    batch_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with zipfile.ZipFile(cache) as archive:
        for info in archive.infolist():
            if not info.filename.lower().endswith((".mp3", ".wav")):
                continue
            target = batch_dir / Path(info.filename).name
            if target.exists() and not force:
                continue
            with archive.open(info) as source, target.open("wb") as sink:
                sink.write(source.read())
            extracted.append(target.name)

    metadata = {
        "source_platform": policy.platform,
        "source_course": batch,
        "license_status": "BY-NC-ND-3.0-US",
        "license_reference": "https://www.goethe-verlag.com/agb.php",
        "accent_claim": "en-GB",
        "title_prefix": "book2 English UK",
        "artifact_url": policy.artifact_url,
        "artifact_sha256": download["sha256"],
    }
    (batch_dir / "batch.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "platform": policy.platform,
        "active_source": policy.platform,
        "download": download,
        "batch_dir": str(batch_dir),
        "extracted": len(extracted),
        "text_required": True,
        "text_blocker": policy.blocker if policy.text_mode != "official-pack" else None,
        "policy": asdict(policy),
        "note": "audio staged only; transcripts are still a human/ASR step before approval",
    }
