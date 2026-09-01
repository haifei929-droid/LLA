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
            page.goto(BASE, wait_until="networkidle")
            page.locator(".training-hero .primary").click()
            page.wait_for_selector(".dictation-panel", timeout=8000)

            # 验证 A：快速连点不产生重复 Sentence completion
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
            a1 = attempt_count(1)
            print(f"验证A 句1 attempt 数 = {a1}（期望 1）")
            assert a1 == 1, "快速连点产生了重复 Sentence completion"

            # 验证 B：refetch 失败 → 显式 Retry UI → 点击恢复
            fail_next = {"on": True}

            def handle_route(route):
                if fail_next["on"]:
                    fail_next["on"] = False
                    route.fulfill(status=500, content_type="application/json",
                                  body='{"detail":"simulated failure"}')
                else:
                    route.continue_()

            page.route("**/dictation-context", handle_route)
            page.locator("text=播放本句").click()
            page.locator(".dictation-input").fill(SENTENCES[1])
            page.locator("button:has-text('提交')").click()
            page.wait_for_selector("button:has-text('重新加载上下文')", timeout=8000)
            print("验证B refetch 失败 → Retry UI 出现")
            page.locator("button:has-text('重新加载上下文')").click()
            page.wait_for_function(
                "() => document.querySelector('.dictation-progress')?.textContent.includes('已正确 2 / 3')",
                timeout=8000,
            )
            print("验证B 点击 Retry 后恢复（已正确 2 / 3）")

            # 验证 C：最后一句完成 → Part completion → 进入 Part 2
            page.locator("text=播放本句").click()
            page.locator(".dictation-input").fill(SENTENCES[2])
            page.locator("button:has-text('提交')").click()
            page.wait_for_selector("button:has-text('完成 Part')", timeout=8000)
            print("验证C 句3完成 → 出现「完成 Part」按钮")
            page.locator("button:has-text('完成 Part')").click()
            page.wait_for_function(
                "() => document.querySelector('.dictation-progress')?.textContent.includes('Part 2')",
                timeout=8000,
            )
            print("验证C 点击「完成 Part」→ 进入 Part 2，无永久 loading")

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
