"""The local page extractor must not invent text and must not keep navigation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "extract_phrasebook_text.py"

HTML = """<html><head><style>.x{background:url(a)}</style><script>var a = "Hello there.";</script></head>
<body>
<nav><a>Home</a> <a>Phrasebook</a> <a>Download monolingual audios</a></nav>
<table>
<tr><td>\u6211\u53eb\u7ea6\u7ff0\u3002</td><td>My name is John.</td></tr>
<tr><td>\u4f26\u6566\u5728\u82f1\u56fd\u3002</td><td>London is in Great Britain.</td></tr>
<tr><td>\u6211\u53eb\u7ea6\u7ff0\u3002</td><td>My name is John.</td></tr>
</table>
<footer>\u00a9 Goethe Verlag GmbH Starnberg 1997-2026. All rights reserved.</footer>
</body></html>"""


def _module():
    spec = importlib.util.spec_from_file_location("extract_phrasebook_text", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extracts_only_english_lesson_lines_and_dedupes() -> None:
    lines = _module().extract_english_lines(HTML)
    assert lines == ["My name is John.", "London is in Great Britain."]


def test_directory_writes_one_txt_per_page(tmp_path: Path) -> None:
    page = tmp_path / "ENEN001.html"
    page.write_text(HTML, encoding="utf-8")
    results = _module().extract_directory(tmp_path)
    assert results and results[0]["lines"] == 2
    written = (tmp_path / "ENEN001.txt").read_text(encoding="utf-8").splitlines()
    assert written == ["My name is John.", "London is in Great Britain."]
