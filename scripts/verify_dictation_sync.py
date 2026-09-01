import json
import os
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

VENV_PY = r"D:\codex\LLA\.venv\Scripts\python.exe"
BASE = "http://127.0.0.1:8002"

SENTENCES = [
    "The quick brown fox jumps over the lazy dog.",
    "She sells seashells by the seashore.",
    "I am sure you would have gone.",
    "The running dogs do not stop.",
    "He has been working all day.",
    "We will meet them tomorrow.",
    "They could not find the way.",
    "It was raining heavily last night.",
    "Everyone enjoyed the party very much.",
]


def api(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else None


def main():
    tmpdir = Path(tempfile.mkdtemp(prefix="lla-dict-e2e-"))
    db_path = tmpdir / "e2e.sqlite3"
    env = dict(os.environ)
    env["LTA_DATABASE_PATH"] = str(db_path)
    proc = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", "8002"],
        cwd=r"D:\codex\LLA", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(4)

        api("POST", "/api/materials", {
            "material_id": "dict-001",
            "title": "Dictation sync material",
            "audio_path": "data/materials/dict.wav",
            "transcript": " ".join(SENTENCES),
            "timestamped_sentences": [
                {"text": text, "start_time": i * 4.0, "end_time": (i + 1) * 4.0}
                for i, text in enumerate(SENTENCES)
            ],
        })
        api("POST", "/api/materials/dict-001/first-listen/complete")
        api("POST", "/api/materials/dict-001/comprehension-check",
            {"phase": "FIRST", "self_rating": "50\u201370%", "summary": "ok"})

        def attempt_count(sentence_index):
            conn = sqlite3.connect(db_path)
            n = conn.execute(
                "SELECT COUNT(*) FROM dictation_attempts WHERE sentence_id = ?",
                (f"dict-001-sentence-{sentence_index:03d}",),
            ).fetchone()[0]
            conn.close()
            return n

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            part_completion_calls = []
            page.on("request", lambda req: part_completion_calls.append(req.url)
                    if "/dictation-parts/" in req.url else None)

            page.goto(BASE, wait_until="networkidle")
            page.locator(".training-hero .primary").click()
            page.wait_for_selector(".dictation-panel", timeout=8000)

            # 句 1：快速连点不产生重复 attempt（operation_id 幂等 + 前端 ref 守卫）
            page.locator("text=播放本句").click()
            page.locator(".dictation-input").fill(SENTENCES[0])
            page.evaluate("""() => {
              const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '提交')
              btn.click(); btn.click();
            }""")
            page.wait_for_function(
                "() => document.querySelector('.dictation-progress')?.textContent.includes('已正确 1 / 3')",
                timeout=8000,
            )
            print(f"句1 后 attempt 数 = {attempt_count(1)}（期望 1）")
            assert attempt_count(1) == 1

            # 句 2：普通句 → 服务端返回 next_context 直接渲染句 3
            page.locator("text=播放本句").click()
            page.locator(".dictation-input").fill(SENTENCES[1])
            page.locator("button:has-text('提交')").click()
            page.wait_for_function(
                "() => document.querySelector('.dictation-progress')?.textContent.includes('已正确 2 / 3')",
                timeout=8000,
            )
            print("句2 后自动进入句3（已正确 2/3）")

            # 句 3：末句 → 服务端原子完成 Part 1 → 直接进入 Part 2
            page.locator("text=播放本句").click()
            page.locator(".dictation-input").fill(SENTENCES[2])
            page.locator("button:has-text('提交')").click()
            page.wait_for_function(
                "() => document.querySelector('.dictation-progress')?.textContent.includes('Part 2')",
                timeout=8000,
            )
            print("句3 后原子进入 Part 2（无刷新、无完成 Part 按钮）")

            # 主路径不应再调用 deprecated Part completion API
            assert not any("/dictation-parts/" in u for u in part_completion_calls), \
                f"主路径调用了 Part completion API: {part_completion_calls}"
            print("主路径未调用 /dictation-parts/ API")

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
