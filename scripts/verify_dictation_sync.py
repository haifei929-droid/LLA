import json
import os
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
    env = dict(os.environ)
    env["LTA_DATABASE_PATH"] = str(tmpdir / "e2e.sqlite3")
    proc = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", "8002"],
        cwd=r"D:\codex\LLA", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(4)

        # Seed a 9-sentence material and advance to DICTATION_PART_1.
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

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(BASE, wait_until="networkidle")
            # Open the material from the homepage hero.
            page.locator(".training-hero .primary").click()
            page.wait_for_selector(".dictation-panel", timeout=8000)

            for idx in range(3):  # Part 1 has sentences 1..3
                # Play once to unlock input (listenCount >= 1).
                page.locator("text=播放本句").click()
                textarea = page.locator(".dictation-input")
                textarea.fill(SENTENCES[idx])
                page.locator("button:has-text('提交')").click()
                if idx < 2:
                    # Wait until the context re-fetch marks sentence idx+1 exact.
                    page.wait_for_function(
                        "() => document.querySelector('.dictation-progress')?.textContent.includes('已正确 %d / 3')" % (idx + 1),
                        timeout=8000,
                    )
                    progress = page.locator(".dictation-progress").inner_text()
                    print(f"after sentence {idx+1}: {progress!r}")
                    assert f"/ 3" in progress, "progress should stay on Part 1"
                else:
                    # Final sentence of the Part -> completion button, not loading.
                    page.wait_for_selector("button:has-text('完成 Part')", timeout=8000)
                    print("after sentence 3: completion button present")
                    print("no permanent loading:", page.locator("text=正在加载听写上下文").count() == 0)

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
