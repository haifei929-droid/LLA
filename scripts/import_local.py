"""Import a manually downloaded material batch into the LLA pool.

This is a *local* tool: it never fetches anything. You download the material
yourself (browser), place audio + transcript in data/import/inbox/<batch>/, and
run this script. Two steps, deliberately separate:

    # 1) import -> QUARANTINED, difficulty measured, transcript unverified
    .venv/Scripts/python.exe scripts/import_local.py --batch enen

    # 2) after you actually listen and check the alignment, approve:
    .venv/Scripts/python.exe scripts/import_local.py --batch enen \
        --approve 50LANGUAGES:book2-en-uk:ENEN001 \
        --accent en-GB --license CLEARED

Nothing here relaxes a gate: approval writes the accent you explicitly pass and
marks the transcript verified because *you* say you checked it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings  # noqa: E402
from app.core.local_import import LocalImportError, LocalImportService, inbox_root  # noqa: E402
from app.db.connection import Database  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a local material batch (no network access)")
    parser.add_argument("--batch", required=True, help="batch folder name under data/import/inbox")
    parser.add_argument("--inbox", default=None, help="override the inbox directory")
    parser.add_argument("--approve", nargs="*", default=None, metavar="MATERIAL_ID", help="promote these material ids into the pool")
    parser.add_argument("--accent", default="en-GB", help="accent to record on approval (default en-GB)")
    parser.add_argument("--license", default="CLEARED", help="license status to record on approval")
    parser.add_argument("--no-transcript-verified", action="store_true", help="approve without claiming transcript verification")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    database = Database(settings)
    database.initialize()
    service = LocalImportService(database, settings)

    root = Path(args.inbox) if args.inbox else inbox_root(settings)
    batch_dir = root / args.batch

    try:
        if args.approve:
            result = service.approve(
                args.approve,
                accent=args.accent,
                license_status=args.license,
                transcript_verified=not args.no_transcript_verified,
            )
            print(json.dumps({"mode": "approve", **result}, ensure_ascii=False, indent=2))
            return 0 if result["approved"] else 2

        result = service.import_batch(batch_dir)
        print(json.dumps({"mode": "import", **result}, ensure_ascii=False, indent=2))
        return 0 if not result["failed"] else 3
    except LocalImportError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())