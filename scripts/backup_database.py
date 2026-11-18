"""Online, verified SQLite backup for the LLA production database.

Why this exists
---------------
On 2026-11-17 the production database (``data/language_training.sqlite3``) was
deleted by a scratch verification script. No copy existed anywhere: the file is
git-ignored, the remote never had it, and there was no local or volume-shadow
backup. This script is the copy that was missing.

Deliberate design rules:
  * standard library only - no new dependency, no crawler, no service;
  * uses the SQLite online backup API, so it is safe while the app is running;
  * every backup is verified (``PRAGMA integrity_check``) and hashed (sha256);
  * the backup directory lives outside the repository and outside ``data/``;
  * retention only ever deletes an explicit, sorted, prefix-matched list of
    files *inside* the backup directory - never a wildcard glob.

Usage:
    .venv/Scripts/python.exe scripts/backup_database.py
    .venv/Scripts/python.exe scripts/backup_database.py --verify

Exit codes: 0 ok, 1 unexpected error, 2 source database missing,
            3 verification failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "language_training.sqlite3"
BACKUP_PREFIX = "language_training-"
INDEX_NAME = "backup-index.jsonl"
DEFAULT_RETENTION = 14


def default_backup_dir() -> Path:
    """Backups must outlive the repository, so they live next to it, not in it."""
    override = os.getenv("LLA_BACKUP_DIR")
    if override:
        return Path(override)
    return PROJECT_ROOT.parent / "LLA-backups" / "db"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_database(source: Path, target: Path) -> None:
    """Consistent copy through the SQLite backup API (safe while in use)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect("file:" + source.as_posix() + "?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def inspect(database: Path) -> dict:
    connection = sqlite3.connect("file:" + database.as_posix() + "?mode=ro&immutable=1", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    finally:
        connection.close()
    return {"integrity": integrity, "tables": tables}


def rotate(backup_dir: Path, retention: int) -> list[str]:
    """Delete only an explicit, prefix-matched, timestamp-ordered list.

    No globbing and no recursion: the set of deletable paths is computed from
    the backup directory's own entries, restricted to our prefix, and each
    deletion target is re-checked to sit directly inside the backup directory.
    """
    if retention <= 0 or not backup_dir.is_dir():
        return []
    root = backup_dir.resolve()
    candidates = sorted(
        (
            entry
            for entry in backup_dir.iterdir()
            if entry.is_file() and entry.name.startswith(BACKUP_PREFIX) and entry.suffix == ".sqlite3"
        ),
        key=lambda entry: entry.name,
    )
    removed: list[str] = []
    for stale in candidates[:-retention]:
        if stale.resolve().parent != root:
            continue
        stale.unlink()
        removed.append(stale.name)
    return removed


def append_index(backup_dir: Path, record: dict) -> Path:
    index = backup_dir / INDEX_NAME
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return index


def verify_mode(backup_dir: Path) -> int:
    if not backup_dir.is_dir():
        print(json.dumps({"ok": False, "error": "backup directory missing", "dir": str(backup_dir)}, ensure_ascii=False))
        return 2
    results: list[dict] = []
    failed = 0
    for entry in sorted(backup_dir.iterdir(), key=lambda item: item.name):
        if not (entry.is_file() and entry.name.startswith(BACKUP_PREFIX) and entry.suffix == ".sqlite3"):
            continue
        try:
            report = inspect(entry)
        except sqlite3.DatabaseError as error:
            report = {"integrity": "error: " + str(error), "tables": 0}
        digest = sha256_file(entry)
        sidecar = entry.with_suffix(entry.suffix + ".sha256")
        sidecar_matches = None
        if sidecar.is_file():
            sidecar_matches = sidecar.read_text(encoding="utf-8").split()[0] == digest
        ok = report["integrity"] == "ok" and sidecar_matches is not False
        failed += 0 if ok else 1
        results.append(
            {
                "backup": entry.name,
                "size": entry.stat().st_size,
                "sha256": digest,
                "integrity": report["integrity"],
                "tables": report["tables"],
                "sidecar_matches": sidecar_matches,
                "ok": ok,
            }
        )
    print(json.dumps({"ok": failed == 0, "checked": len(results), "failed": failed, "backups": results}, ensure_ascii=False))
    return 0 if failed == 0 else 3


def run_backup(database: Path, backup_dir: Path, retention: int) -> int:
    if not database.is_file():
        print(json.dumps({"ok": False, "error": "source database missing", "database": str(database)}, ensure_ascii=False))
        return 2
    backup_dir.mkdir(parents=True, exist_ok=True)
    source_digest = sha256_file(database)
    target = backup_dir / (BACKUP_PREFIX + utc_stamp() + ".sqlite3")
    copy_database(database, target)

    report = inspect(target)
    if report["integrity"] != "ok":
        quarantine = target.with_suffix(".corrupt")
        target.replace(quarantine)
        print(
            json.dumps(
                {"ok": False, "error": "backup failed integrity check", "quarantine": str(quarantine), "integrity": report["integrity"]},
                ensure_ascii=False,
            )
        )
        return 3

    backup_digest = sha256_file(target)
    sidecar = target.with_suffix(target.suffix + ".sha256")
    sidecar.write_text(backup_digest + "  " + target.name + "\n", encoding="utf-8")

    removed = rotate(backup_dir, retention)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source": str(database),
        "source_sha256": source_digest,
        "backup": str(target),
        "backup_sha256": backup_digest,
        "size": target.stat().st_size,
        "tables": report["tables"],
        "integrity": report["integrity"],
        "removed_by_retention": removed,
        "ok": True,
    }
    record["index"] = str(append_index(backup_dir, record))
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified online backup of the LLA database")
    parser.add_argument("--database", default=str(DEFAULT_DB), help="source SQLite file")
    parser.add_argument("--backup-dir", default=str(default_backup_dir()), help="destination directory, outside the repo")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION, help="how many backups to keep")
    parser.add_argument("--verify", action="store_true", help="verify existing backups instead of creating one")
    args = parser.parse_args(argv)

    backup_dir = Path(args.backup_dir).expanduser()
    resolved = backup_dir.resolve()
    repo_root = PROJECT_ROOT.resolve()
    if resolved == repo_root or repo_root in resolved.parents:
        print(json.dumps({"ok": False, "error": "backup directory must stay outside the repository", "dir": str(resolved)}, ensure_ascii=False))
        return 1

    if args.verify:
        return verify_mode(backup_dir)
    return run_backup(Path(args.database).expanduser(), backup_dir, args.retention)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - a scheduled task must report, not vanish
        print(json.dumps({"ok": False, "error": type(error).__name__ + ": " + str(error)}, ensure_ascii=False))
        sys.exit(1)