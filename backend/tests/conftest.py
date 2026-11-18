"""Suite-level data-safety guards for the LLA backend tests.

Historical context: on 2026-11-17 a scratch verification script ran a
non-recursive ``glob("*.sqlite3")`` plus ``unlink`` against
``D:\\CODEX\\LLA\\data`` and deleted the production database. Two guards below
make the suite structurally unable to repeat that accident:

1. every ``LTA_*`` path is forced into a throwaway runtime directory outside
   ``data/`` for the whole session, and an inherited value that points inside
   ``data/`` aborts the run instead of silently using production data;
2. the production database is fingerprinted before and after the session, and
   any change fails the run.

Neither guard weakens a test assertion: fixtures that build their own
``Settings(tmp_path=...)`` keep doing exactly that.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_DATA_DIR = PROJECT_ROOT / "data"
PRODUCTION_DB = PRODUCTION_DATA_DIR / "language_training.sqlite3"
ISOLATED_ENV = (
    "LTA_DATABASE_PATH",
    "LTA_MATERIALS_DIR",
    "LTA_RECORDINGS_DIR",
    "LTA_PROCESSED_DIR",
)


def _inside_production_data(value: str) -> bool:
    try:
        candidate = Path(value).expanduser().resolve()
    except OSError:
        return False
    try:
        candidate.relative_to(PRODUCTION_DATA_DIR.resolve())
    except ValueError:
        return False
    return True


def _fingerprint(path: Path) -> tuple[int, str] | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return path.stat().st_size, digest.hexdigest()


@pytest.fixture(scope="session", autouse=True)
def isolated_runtime(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Force every LTA_* path into a session-scoped runtime dir outside data/."""
    for name in ISOLATED_ENV:
        inherited = os.environ.get(name)
        if inherited and _inside_production_data(inherited):
            raise pytest.UsageError(
                name + "=" + inherited + " points inside the production data directory "
                + str(PRODUCTION_DATA_DIR)
                + "; tests must run against an isolated runtime directory."
            )

    runtime = tmp_path_factory.mktemp("lla-runtime")
    saved = {name: os.environ.get(name) for name in ISOLATED_ENV}
    os.environ.update(
        {
            "LTA_DATABASE_PATH": str(runtime / "language_training.sqlite3"),
            "LTA_MATERIALS_DIR": str(runtime / "materials"),
            "LTA_RECORDINGS_DIR": str(runtime / "recordings"),
            "LTA_PROCESSED_DIR": str(runtime / "processed"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    try:
        yield runtime
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(scope="session", autouse=True)
def production_database_untouched(isolated_runtime: Path) -> None:
    """Fail the run if the suite creates, deletes or modifies the production DB."""
    before = _fingerprint(PRODUCTION_DB)
    yield
    after = _fingerprint(PRODUCTION_DB)
    assert after == before, (
        "the test suite modified the production database at "
        + str(PRODUCTION_DB)
        + " (before=" + repr(before) + ", after=" + repr(after) + "). "
        "Tests must write only to their isolated runtime directory."
    )