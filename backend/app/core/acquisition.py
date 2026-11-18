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


_CHALLENGE_MARKERS = ("just a moment", "cf-chl", "attention required", "checking your browser", "enable javascript and cookies")


def _probe_page(url: str, timeout: int = 30) -> dict[str, Any]:
    """Fetch at most 2 KB and say whether it is a bot challenge, not the page."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-2047"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2048).decode("utf-8", "replace").lower()
            challenged = any(marker in body for marker in _CHALLENGE_MARKERS)
            return {
                "url": url,
                "status": response.status,
                "bytes_read": len(body),
                "served_challenge": challenged,
            }
    except urllib.error.HTTPError as error:
        return {"url": url, "status": error.code, "served_challenge": True}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"url": url, "status": None, "served_challenge": True, "error": str(error)}


def probe_source(policy: SourcePolicy) -> dict[str, Any]:
    """Reachability of what the policy says we may (or may not) fetch."""
    report: dict[str, Any] = {
        "platform": policy.platform,
        "audio_mode": policy.audio_mode,
        "text_mode": policy.text_mode,
        "blocker": policy.blocker,
    }
    if policy.artifact_url:
        report["artifact"] = {"url": policy.artifact_url, **_head(policy.artifact_url)}
    # The lesson pages are probed only to *record* the blocker, never to fetch text.
    page = {
        "50LANGUAGES": "https://www.goethe-verlag.com/book2/ZH/ZHEN/ZHEN001.HTM",
        "BRITISH_COUNCIL": "https://learnenglish.britishcouncil.org/terms-use",
        "BREAKING_NEWS_ENGLISH": "https://breakingnewsenglish.com/copyright.html",
    }[policy.platform]
    report["lesson_page"] = _probe_page(page)
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
def _download(url: str, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response, target.open("wb") as handle:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            digest.update(block)
            handle.write(block)
    return {"url": url, "path": str(target), "bytes": target.stat().st_size, "sha256": digest.hexdigest()}


def fetch_official_pack(
    platform: str,
    inbox_root: Path,
    *,
    batch: str = "book2-en-uk",
    force: bool = False,
) -> dict[str, Any]:
    """Fetch the publisher's own audio pack and stage it for the import path.

    Nothing is registered in the database here: the pack lands in the manual
    import inbox, ``batch.json`` records where it came from and under which
    licence, and the governed import path still quarantines it until a human
    approves. Auto-ingestion into the pool is intentionally *not* implemented.
    """
    policy = assert_can_automate(platform)
    assert policy.artifact_url is not None
    name = policy.artifact_url.rsplit("/", 1)[-1]
    cache = inbox_root.parent / "cache" / name
    batch_dir = inbox_root / batch

    if force or not cache.is_file():
        download = _download(policy.artifact_url, cache)
    else:
        download = {
            "url": policy.artifact_url,
            "path": str(cache),
            "bytes": cache.stat().st_size,
            "sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
            "cached": True,
        }

    batch_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with zipfile.ZipFile(cache) as archive:
        broken = archive.testzip()
        if broken is not None:
            raise AcquisitionError("CORRUPT_ARCHIVE", f"zip member failed its CRC check: {broken}")
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
        "download": download,
        "batch_dir": str(batch_dir),
        "extracted": len(extracted),
        "text_required": True,
        "text_blocker": policy.blocker if policy.text_mode != "official-pack" else None,
        "policy": asdict(policy),
        "note": "audio staged only; transcripts are still a human/ASR step before approval",
    }
