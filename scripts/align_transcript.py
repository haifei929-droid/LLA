"""Align a corrected transcript against the audio and emit sentence boundaries.

Pipeline position: after you have a transcript you trust (your own typing, the
page extractor, or an ASR draft you corrected) and before importing.

    .venv/Scripts/python.exe scripts/align_transcript.py --batch book2-en-uk --model small

For every ``<stem>.mp3`` with a ``<stem>.txt`` it produces ``<stem>.aligned.json``:

    {"lines": [{"text", "start", "end", "repeats", "coverage"}], "unaligned": [...], ...}

``unaligned`` must be empty (or fixed) before the import will accept the file: a
line that does not match the audio is reported, never given a made-up boundary.
Nothing here touches the database, so running it is always safe.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.asr_draft import ASRUnavailable, transcribe_words  # noqa: E402
from app.core.forced_alignment import align_lines  # noqa: E402
from app.core.local_import import AUDIO_SUFFIXES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Align corrected transcripts to word timestamps")
    parser.add_argument("--batch", default="book2-en-uk")
    parser.add_argument("--inbox", default=str(Path(__file__).resolve().parents[1] / "data" / "import" / "inbox"))
    parser.add_argument("--model", default="small", help="faster-whisper model for word timestamps")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="re-align even when a result exists")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    batch = Path(args.inbox) / args.batch
    if not batch.is_dir():
        print(json.dumps({"ok": False, "error": f"no batch directory at {batch}"}, ensure_ascii=False))
        return 2

    audios = [p for p in sorted(batch.iterdir()) if p.suffix.lower() in AUDIO_SUFFIXES]
    if args.limit:
        audios = audios[: args.limit]

    done: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    for audio in audios:
        transcript = audio.with_suffix(".txt")
        target = audio.with_suffix(".aligned.json")
        if not transcript.is_file():
            skipped.append({"audio": audio.name, "reason": "no transcript next to the audio"})
            continue
        if target.is_file() and not args.force:
            skipped.append({"audio": audio.name, "reason": "already aligned (use --force)"})
            continue
        lines = [line.strip() for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            skipped.append({"audio": audio.name, "reason": "empty transcript"})
            continue
        try:
            result = transcribe_words(audio, model_size=args.model)
        except ASRUnavailable as error:
            failed.append({"audio": audio.name, "error": str(error)})
            break  # a missing model will not fix itself for the next file
        alignment = align_lines(lines, result["words"])
        payload = {"audio": audio.name, "model": result["model"], **alignment.as_dict()}
        if not args.dry_run:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        done.append(
            {
                "audio": audio.name,
                "lines": len(alignment.lines),
                "unaligned": len(alignment.unaligned),
                "coverage": alignment.coverage,
                "repeats": sum(1 for line in alignment.lines if line.repeats > 1),
                "written": None if args.dry_run else target.name,
            }
        )

    print(
        json.dumps(
            {"mode": "align", "model": args.model, "aligned": done, "skipped": skipped, "failed": failed},
            ensure_ascii=False,
            indent=2,
        )
    )
    if failed:
        return 4
    return 0 if done else 3


if __name__ == "__main__":
    sys.exit(main())
