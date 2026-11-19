"""Fetch the official English (UK) audio pack and stage it for the import path.

Nothing here scrapes lesson pages: the three candidate platforms block or forbid
that (see docs/LLA_Primary_Source_Authorization_Assessment.md). What it does is
fetch the single audio pack the publisher offers - CC BY-NC-ND 3.0 US audio -
unpack it into ``data/import/inbox/<batch>/`` and write ``batch.json`` so the
governed import path can quarantine it.

    # 1) is the world still what the policy says it is? (live HEAD probes)
    .venv/Scripts/python.exe scripts/fetch_materials.py --probe

    # 2) fetch + unpack the official pack (no database writes)
    .venv/Scripts/python.exe scripts/fetch_materials.py --fetch

    # 3) optional: draft transcripts from the audio (review them before import)
    .venv/Scripts/python.exe scripts/fetch_materials.py --draft-asr --limit 3

    # 4) is the local reserve short? plan a top-up
    .venv/Scripts/python.exe scripts/fetch_materials.py --topup --days 7

After that: ``scripts/import_local.py --batch <batch>`` then ``--approve``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings  # noqa: E402
from app.core.acquisition import (  # noqa: E402
    AcquisitionError,
    fetch_official_pack,
    first_automation_allowed,
    probe_all,
    reserve_plan,
)
from app.core.asr_draft import ASRUnavailable, write_draft  # noqa: E402
from app.core.local_import import AUDIO_SUFFIXES, inbox_root  # noqa: E402
from app.core.material_pool import MaterialPoolService  # noqa: E402
from app.db.connection import Database  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed acquisition of the English (UK) audio pack")
    parser.add_argument("--probe", action="store_true", help="live HEAD probes: what is reachable, what is blocked")
    parser.add_argument("--fetch", action="store_true", help="fetch and unpack the official audio pack")
    parser.add_argument("--topup", action="store_true", help="plan (and if short, run) a reserve top-up")
    parser.add_argument("--draft-asr", action="store_true", help="draft transcripts for staged audio")
    parser.add_argument("--days", type=int, default=7, help="reserve length in days (default 7)")
    parser.add_argument("--per-day", type=float, default=1.0, help="materials consumed per day (default 1)")
    parser.add_argument("--batch", default="book2-en-uk")
    parser.add_argument("--scope", default="default", help="governance scope for the active-source and band checks")
    parser.add_argument("--inbox", default=None)
    parser.add_argument("--limit", type=int, default=0, help="limit ASR drafts (0 = no limit)")
    parser.add_argument("--force", action="store_true", help="re-download / overwrite staged files")
    parser.add_argument("--dry-run", action="store_true", help="plan only, do not fetch")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    root = Path(args.inbox) if args.inbox else inbox_root(settings)
    batch_dir = root / args.batch

    try:
        if args.probe:
            print(json.dumps({"mode": "probe", "sources": probe_all()}, ensure_ascii=False, indent=2))
            return 0

        if args.fetch:
            policy = first_automation_allowed()
            database = Database(settings)
            database.initialize()
            result = fetch_official_pack(
                database, policy.platform, root, batch=args.batch, force=args.force, scope_id=args.scope
            )
            print(json.dumps({"mode": "fetch", **result}, ensure_ascii=False, indent=2))
            return 0

        if args.topup:
            database = Database(settings)
            database.initialize()
            ready = MaterialPoolService(database, scope_id=args.scope).status()["selectable_in_band"]
            plan = reserve_plan(ready, per_day=args.per_day, days=args.days)
            if plan["should_topup"] and not args.dry_run:
                policy = first_automation_allowed()
                plan["fetch"] = fetch_official_pack(
                    database, policy.platform, root, batch=args.batch, force=args.force, scope_id=args.scope
                )
            elif not plan["should_topup"]:
                plan["fetch"] = None
            print(json.dumps({"mode": "topup", **plan}, ensure_ascii=False, indent=2))
            return 0

        if args.draft_asr:
            audios = [p for p in sorted(batch_dir.iterdir()) if p.suffix.lower() in AUDIO_SUFFIXES] if batch_dir.is_dir() else []
            if args.limit:
                audios = audios[: args.limit]
            drafted, failed = [], []
            for audio in audios:
                if audio.with_suffix(".txt").exists():
                    drafted.append({"audio": audio.name, "skipped": "transcript already exists"})
                    continue
                try:
                    drafted.append(write_draft(audio))
                except ASRUnavailable as error:
                    failed.append({"audio": audio.name, "error": str(error)})
                    break  # a missing model will not fix itself for the next file
            print(json.dumps({"mode": "draft-asr", "drafted": drafted, "failed": failed}, ensure_ascii=False, indent=2))
            return 0 if drafted and not failed else 4

        parser.print_help()
        return 0
    except AcquisitionError as error:
        print(json.dumps({"ok": False, "code": error.code, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
