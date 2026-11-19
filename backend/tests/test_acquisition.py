"""Governed acquisition: policy gate, active-source gate, reserve maths, atomic cache."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.core import acquisition
from app.core.material_candidates import MaterialCandidateService
from tests.fixtures import make_database, make_settings


def _activate(database, tmp_path: Path, platform: str = "50LANGUAGES") -> None:
    MaterialCandidateService(database, make_settings(tmp_path)).activate_source(
        "default",
        platform,
        license_reference="fixture-license",
        catalog_scope="fixture-scope",
        confirmed_by="TEST",
    )


def _zip_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("ENEN001.mp3", b"ID3fake-audio-1")
        archive.writestr("ENEN002.mp3", b"ID3fake-audio-2")
        archive.writestr("_readme.htm", b"<html>readme</html>")
    return payload.getvalue()


def _patch_download(monkeypatch, payload: bytes, calls: list[str]) -> None:
    def fake(url: str, target: Path) -> dict:
        calls.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return {"url": url, "bytes": len(payload), "sha256": "deadbeef"}

    monkeypatch.setattr(acquisition, "_download", fake)


def test_policy_refuses_platforms_that_forbid_automation() -> None:
    for platform in ("BRITISH_COUNCIL", "BREAKING_NEWS_ENGLISH"):
        with pytest.raises(acquisition.AcquisitionError) as excinfo:
            acquisition.assert_can_automate(platform)
        assert excinfo.value.code == "SOURCE_BLOCKED_BY_POLICY"
    allowed = acquisition.assert_can_automate("50LANGUAGES")
    assert allowed.audio_mode == "official-pack"
    assert allowed.text_mode == "manual"


def test_first_allowed_source_is_the_publisher_pack() -> None:
    policy = acquisition.first_automation_allowed()
    assert policy.platform == "50LANGUAGES"
    assert policy.artifact_url and policy.artifact_url.endswith("ENEN.ZIP")


def test_reserve_plan_math() -> None:
    assert acquisition.reserve_plan(0, per_day=1, days=7)["shortfall"] == 7
    assert acquisition.reserve_plan(10, per_day=1, days=7)["should_topup"] is False
    assert acquisition.reserve_plan(3, per_day=0.5, days=7)["needed"] == 4
    assert acquisition.reserve_plan(0, per_day=0, days=7)["shortfall"] == 0
    with pytest.raises(acquisition.AcquisitionError):
        acquisition.reserve_plan(0, per_day=-1, days=7)


def test_fetch_requires_the_active_source(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _patch_download(monkeypatch, _zip_bytes(), [])

    with pytest.raises(acquisition.AcquisitionError) as no_source:
        acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")
    assert no_source.value.code == "SOURCE_NOT_APPROVED"

    _activate(database, tmp_path, "BRITISH_COUNCIL")
    with pytest.raises(acquisition.AcquisitionError) as mismatch:
        acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")
    assert mismatch.value.code == "SOURCE_MISMATCH"


def test_fetch_unpacks_audio_and_writes_batch_metadata(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _activate(database, tmp_path)
    calls: list[str] = []
    _patch_download(monkeypatch, _zip_bytes(), calls)

    result = acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox", batch="book2-en-uk")

    batch = tmp_path / "inbox" / "book2-en-uk"
    assert result["extracted"] == 2
    assert sorted(p.name for p in batch.glob("*.mp3")) == ["ENEN001.mp3", "ENEN002.mp3"]
    assert not (batch / "_readme.htm").exists()
    metadata = (batch / "batch.json").read_text(encoding="utf-8")
    assert "50LANGUAGES" in metadata and "BY-NC-ND-3.0-US" in metadata
    assert result["text_required"] is True


def test_valid_cache_is_reused_without_downloading(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _activate(database, tmp_path)
    calls: list[str] = []
    _patch_download(monkeypatch, _zip_bytes(), calls)

    acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")
    second = acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")

    assert len(calls) == 1, "a valid cache must not be downloaded again"
    assert second["download"]["cached"] is True


def test_partial_download_is_discarded_and_a_plain_retry_works(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _activate(database, tmp_path)
    calls: list[str] = []
    cache = tmp_path / "cache" / "ENEN.ZIP"
    part = cache.with_suffix(".ZIP.part")

    _patch_download(monkeypatch, b"this-is-not-a-zip", calls)
    with pytest.raises(acquisition.AcquisitionError) as excinfo:
        acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")
    assert excinfo.value.code == "INCOMPLETE_DOWNLOAD"
    assert not cache.exists(), "an unverified download must never become the cache"
    assert not part.exists(), "the partial file must be discarded"

    _patch_download(monkeypatch, _zip_bytes(), calls)
    result = acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")
    assert result["extracted"] == 2
    assert cache.is_file()


def test_corrupt_cache_is_replaced_not_trusted(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _activate(database, tmp_path)
    cache = tmp_path / "cache" / "ENEN.ZIP"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"stale-and-broken")

    calls: list[str] = []
    _patch_download(monkeypatch, _zip_bytes(), calls)
    result = acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")

    assert len(calls) == 1
    assert result["download"].get("cached") is None
    assert acquisition.archive_is_valid(cache)


def test_probe_only_touches_what_may_be_acquired(monkeypatch) -> None:
    calls: list[str] = []

    def fake_head(url: str, timeout: int = 30) -> dict:
        calls.append(url)
        return {"status": 200}

    monkeypatch.setattr(acquisition, "_head", fake_head)
    reports = {item["platform"]: item for item in acquisition.probe_all()}

    assert calls == ["https://www.50idiomas.com/_1language/ENEN.ZIP"], "no request may go to a blocked page"
    assert reports["50LANGUAGES"]["probed"] is True
    assert "section 6" in reports["50LANGUAGES"]["blocker"]
    for blocked in ("BRITISH_COUNCIL", "BREAKING_NEWS_ENGLISH"):
        assert reports[blocked]["probed"] is False
        assert "no request sent" in reports[blocked]["skipped"]
        assert reports[blocked]["blocker"]

def test_stale_part_file_is_removed_before_fetching(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _activate(database, tmp_path)
    cache = tmp_path / "cache" / "ENEN.ZIP"
    part = cache.with_suffix(".ZIP.part")
    cache.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"half-a-download")

    calls: list[str] = []
    _patch_download(monkeypatch, _zip_bytes(), calls)
    result = acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")

    assert not part.exists(), "leftover .part debris must be cleaned"
    assert result["extracted"] == 2


def test_corrupt_cache_is_quarantined_then_replaced(tmp_path: Path, monkeypatch) -> None:
    database = make_database(tmp_path)
    _activate(database, tmp_path)
    cache = tmp_path / "cache" / "ENEN.ZIP"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"broken-cache-bytes")

    calls: list[str] = []
    _patch_download(monkeypatch, _zip_bytes(), calls)
    result = acquisition.fetch_official_pack(database, "50LANGUAGES", tmp_path / "inbox")

    corrupt = cache.with_suffix(".ZIP.corrupt")
    assert corrupt.is_file(), "a corrupt cache is evidence, not garbage"
    assert corrupt.read_bytes() == b"broken-cache-bytes"
    assert result["quarantined_cache"] == str(corrupt)
    assert acquisition.archive_is_valid(cache)
    assert result["extracted"] == 2


def test_require_database_never_creates_or_migrates(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with pytest.raises(acquisition.AcquisitionError) as missing:
        acquisition.require_database(settings)
    assert missing.value.code == "DATABASE_MISSING"
    assert not Path(settings.database_path).exists(), "opening a CLI path must not create a database"

    Path(settings.database_path).write_bytes(b"")
    with pytest.raises(acquisition.AcquisitionError) as outdated:
        acquisition.require_database(settings)
    assert outdated.value.code == "SCHEMA_OUTDATED"
    assert Path(settings.database_path).stat().st_size == 0, "no schema may be written by a read path"


def test_require_database_reports_and_then_applies_migration(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    settings = make_settings(tmp_path)
    with database.connect() as connection:
        connection.execute("ALTER TABLE materials DROP COLUMN difficulty_score")
        connection.execute("ALTER TABLE materials DROP COLUMN difficulty_evidence")

    with pytest.raises(acquisition.AcquisitionError) as outdated:
        acquisition.require_database(settings, need_pool_schema=True)
    assert outdated.value.code == "SCHEMA_OUTDATED"
    assert "difficulty_score" in str(outdated.value)

    migrated = acquisition.require_database(settings, need_pool_schema=True, migrate=True)
    with migrated.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(materials)")}
    assert {"difficulty_score", "difficulty_evidence"} <= columns
