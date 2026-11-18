"""Extract lesson text from browser-saved phrasebook pages.

Why this exists
---------------
The publisher's site sits behind bot protection (a Cloudflare challenge page), so
neither a script nor an automated fetcher can read a lesson. A real browser can:
you open the lesson, solve/ignore the challenge as a human, and save the page
(``Ctrl+S``). This tool then works only on those *local* files - it never makes a
network request, never crawls, and never bypasses anything.

Usage::

    # save the page in the browser as data/import/inbox/enen/ENEN001.html
    .venv/Scripts/python.exe scripts/extract_phrasebook_text.py --inbox data/import/inbox/enen

    # writes ENEN001.txt next to it (one sentence per line, English only)

Output rules (deliberately boring, because the HTML is not ours to rely on):
  * drop <script>/<style>, treat block tags as line breaks, strip tags;
  * keep a line only if it contains ASCII letters, no CJK characters, at least
    two words, and does not match a small stop-list of site navigation strings;
  * de-duplicate while preserving order, so the same phrase repeated in the
    layout does not repeat in the transcript.

The result is an approximation of the lesson text: read it before approving the
material (the import path keeps ``transcript_verified = 0`` until you do).
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

_BLOCK_TAGS = r"</?(?:p|div|br|tr|td|th|li|h[1-6]|table|tbody|thead)\b[^>]*>"
_SCRIPT_STYLE = r"(?is)<(script|style)\b.*?</\1>"
_TAG = r"<[^>]+>"
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_WORD = re.compile(r"[A-Za-z]")

#: Substrings that mark a line as site chrome rather than lesson text.
_STOP_SUBSTRINGS = (
    "all rights reserved",
    "goethe verlag",
    "copyright",
    "privacy policy",
    "imprint",
    "terms of use",
    "download bilingual",
    "download monolingual",
    "buy the book",
    "find a teacher",
    "become a teacher",
    "»",
)

#: Navigation / boilerplate that survives tag stripping but is not lesson text.
_STOPLIST = {
    "home",
    "phrasebook",
    "vocabulary",
    "alphabet",
    "tests",
    "apps",
    "video",
    "books",
    "games",
    "schools",
    "radio",
    "teachers",
    "find a teacher",
    "become a teacher",
    "choose your native language",
    "i speak",
    "i want to learn",
    "download bilingual audios",
    "download monolingual audios",
    "buy the book",
    "privacy policy",
    "imprint",
    "contact",
    "terms of use",
    "all rights reserved",
}


def _to_lines(document: str) -> list[str]:
    text = re.sub(_SCRIPT_STYLE, " ", document)
    text = re.sub(_BLOCK_TAGS, "\n", text, flags=re.IGNORECASE)
    text = re.sub(_TAG, " ", text)
    text = html.unescape(text)
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]


def extract_english_lines(document: str) -> list[str]:
    """Return the English-looking lesson lines, in page order, de-duplicated."""
    kept: list[str] = []
    seen: set[str] = set()
    for line in _to_lines(document):
        if len(line) < 3 or len(line) > 240:
            continue
        if _CJK.search(line):
            continue
        if not _WORD.search(line):
            continue
        if len(line.split()) < 2:
            continue
        lowered = line.lower()
        if lowered.strip(" .:!?") in _STOPLIST:
            continue
        if any(marker in lowered for marker in _STOP_SUBSTRINGS):
            continue
        if line in seen:
            continue
        seen.add(line)
        kept.append(line)
    return kept


def _read_document(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_directory(inbox: Path, *, dry_run: bool = False) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for page in sorted(inbox.iterdir()):
        if page.suffix.lower() not in {".htm", ".html"}:
            continue
        lines = extract_english_lines(_read_document(page))
        target = page.with_suffix(".txt")
        if not dry_run and lines:
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        results.append(
            {
                "page": page.name,
                "written": None if dry_run or not lines else target.name,
                "lines": len(lines),
                "preview": lines[:2],
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract lesson text from locally saved phrasebook pages")
    parser.add_argument("--inbox", required=True, help="folder with the browser-saved .htm/.html pages")
    parser.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    args = parser.parse_args(argv)

    inbox = Path(args.inbox)
    if not inbox.is_dir():
        print(f"not a directory: {inbox}")
        return 2
    results = extract_directory(inbox, dry_run=args.dry_run)
    if not results:
        print(f"no .htm/.html file found in {inbox}")
        return 2
    for row in results:
        print(f"{row['page']}: {row['lines']} lines -> {row['written'] or '(dry run)'}")
        for preview in row["preview"]:
            print(f"    | {preview}")
    return 0 if all(row["lines"] for row in results) else 3


if __name__ == "__main__":
    sys.exit(main())
