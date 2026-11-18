"""Static and functional guards against the 2026-11-17 class of data-loss bug.

On 2026-11-17 a scratch script ran a non-recursive ``glob("*.sqlite3")`` plus
``unlink`` inside ``D:\\CODEX\\LLA\\data`` and deleted the production database,
together with the P2 fixture database. Nothing in the repository was watching
for that shape of code, so it shipped silently.

These tests are cheap, run with the normal suite, and fail on the *pattern*
rather than on the accident:

1. no recursive directory deletion in shipped code;
2. no wildcard glob feeding a delete call in shipped code;
3. the backup tool really produces a verified, readable copy and rotates only
   its own files.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("backend/app", "backend/scripts", "scripts")
SCANNED_SUFFIXES = {".py", ".ps1", ".bat", ".cmd"}
SKIP_PARTS = {"__pycache__", "tests", "node_modules", ".venv"}

RECURSIVE_DELETE_PATTERNS = (
    r"shutil\.rmtree\s*\(",
    r"Remove-Item[^\n]{0,120}-Recurse",
    r"\brd\s+/s\b",
    r"\bdel\s+/s\b",
)

GLOB_PATTERNS = (
    r"\.glob\s*\(",
    r"\.rglob\s*\(",
    r"Get-ChildItem\b",
)

DELETE_CALL = re.compile(
    r"(\.unlink\s*\(|\.remove\s*\(|os\.remove\s*\(|os\.unlink\s*\(|Remove-Item\b|shutil\.rmtree)",
    re.IGNORECASE,
)


def _iter_sources() -> list[Path]:
    sources: list[Path] = []
    for relative in SCANNED_DIRS:
        root = PROJECT_ROOT / relative
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
                continue
            if any(part in SKIP_PARTS for part in path.relative_to(PROJECT_ROOT).parts):
                continue
            sources.append(path)
    return sources


def test_no_recursive_directory_deletion_in_shipped_code() -> None:
    offenders: list[str] = []
    for path in _iter_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in RECURSIVE_DELETE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(str(path.relative_to(PROJECT_ROOT)) + ":" + str(line))
    assert not offenders, (
        "recursive directory deletion is banned in shipped code: " + ", ".join(offenders)
    )


def test_no_wildcard_glob_feeding_a_delete_call() -> None:
    """The exact incident shape: a glob or recursive listing feeding a delete."""
    offenders: list[str] = []
    for path in _iter_sources():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if not any(re.search(pattern, line, re.IGNORECASE) for pattern in GLOB_PATTERNS):
                continue
            window = "\n".join(lines[index : index + 6])
            if DELETE_CALL.search(window):
                offenders.append(str(path.relative_to(PROJECT_ROOT)) + ":" + str(index + 1))
    assert not offenders, (
        "a wildcard glob or recursive listing feeding a delete call is banned "
        "(2026-11-17 main-database incident): " + ", ".join(offenders)
    )


def _run_backup(database: Path, backup_dir: Path, retention: int) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "backup_database.py"),
            "--database",
            str(database),
            "--backup-dir",
            str(backup_dir),
            "--retention",
            str(retention),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_backup_script_creates_a_verified_readable_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE materials (id TEXT PRIMARY KEY, title TEXT)")
    connection.execute("INSERT INTO materials VALUES ('m1', 'hello')")
    connection.commit()
    connection.close()

    record = _run_backup(source, tmp_path / "backups", 5)

    assert record["ok"] is True
    assert record["integrity"] == "ok"
    assert record["tables"] == 1
    assert record["size"] == source.stat().st_size
    backup_file = Path(record["backup"])
    assert backup_file.is_file()
    sidecar = backup_file.with_suffix(backup_file.suffix + ".sha256")
    assert sidecar.read_text(encoding="utf-8").split()[0] == record["backup_sha256"]

    restored = sqlite3.connect("file:" + backup_file.as_posix() + "?mode=ro", uri=True)
    try:
        assert restored.execute("SELECT id, title FROM materials").fetchall() == [("m1", "hello")]
    finally:
        restored.close()


def test_backup_retention_only_touches_its_own_files(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    bystander = backup_dir / "keep-me.txt"
    bystander.write_text("do not delete", encoding="utf-8")

    _run_backup(source, backup_dir, 1)
    _run_backup(source, backup_dir, 1)

    backups = sorted(
        entry.name
        for entry in backup_dir.iterdir()
        if entry.is_file() and entry.name.startswith("language_training-") and entry.suffix == ".sqlite3"
    )
    assert len(backups) == 1, backups
    assert bystander.is_file(), "retention must never delete a file it does not own"


def test_backup_refuses_to_write_inside_the_repository(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    sqlite3.connect(source).close()
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "backup_database.py"),
            "--database",
            str(source),
            "--backup-dir",
            str(PROJECT_ROOT / "backups"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 1, completed.stdout
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "outside the repository" in payload["error"]