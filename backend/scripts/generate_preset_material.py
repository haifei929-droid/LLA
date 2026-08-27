"""Generate a real-voice preset material: original English text, per-sentence
TTS synthesis (slow, clear), precise timestamps, and a concatenated WAV."""

from __future__ import annotations

import asyncio
import json
import struct
import subprocess
import wave
from pathlib import Path

SENTENCES = [
    "Rivers have shaped human life for thousands of years.",
    "Early people built their homes beside rivers for good reasons.",
    "The water gave them food and a way to travel.",
    "It also made the land around it rich and green.",
    "Towns grew into cities along these busy waterways.",
    "Boats carried grain, wood, and other goods between distant places.",
    "When people traded with one another, new ideas traveled too.",
    "Farmers used the river water to grow their crops in dry seasons.",
    "Makers of cloth and metal used its power to run their machines.",
    "In time, engineers built bridges across the great rivers.",
    "These bridges connected the two sides of each city.",
    "People who lived far apart could now meet and share their work.",
    "Rivers also gave people a place for rest and play.",
    "Families came to the water to cool off in the summer heat.",
    "Children learned to swim in the calm, shallow pools.",
    "Today we still depend on rivers in many quiet ways.",
    "They bring water to our homes and food to our tables.",
    "And they remind us how nature and human life work together.",
]

VOICE = "en-US-JennyNeural"
RATE = "-25%"
SILENCE_BETWEEN = 0.6  # seconds between sentences
OUT_DIR = Path(r"D:\codex\LLA\data\materials\preset-001")
RATE_HZ = 16000


async def synth_sentence(text: str, mp3_path: Path) -> None:
    from edge_tts import Communicate

    com = Communicate(text, VOICE, rate=RATE)
    await com.save(str(mp3_path))


def mp3_to_wav(mp3_path: Path, wav_path: Path) -> float:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(mp3_path),
            "-ac", "1", "-ar", str(RATE_HZ), "-sample_fmt", "s16",
            str(wav_path),
        ],
        check=True,
    )
    with wave.open(str(wav_path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def concat_wavs(sentence_wavs: list[Path], output: Path) -> None:
    silence = struct.pack("<h", 0)
    silence_frames = int(SILENCE_BETWEEN * RATE_HZ)
    with wave.open(str(output), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE_HZ)
        for index, wav_path in enumerate(sentence_wavs):
            with wave.open(str(wav_path), "rb") as source:
                out.writeframes(source.readframes(source.getnframes()))
            if index < len(sentence_wavs) - 1:
                for _ in range(silence_frames):
                    out.writeframes(silence)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mp3_paths = [OUT_DIR / f"sentence-{index:03d}.mp3" for index in range(len(SENTENCES))]
    wav_paths = [OUT_DIR / f"sentence-{index:03d}.wav" for index in range(len(SENTENCES))]

    for index, (text, mp3_path) in enumerate(zip(SENTENCES, mp3_paths)):
        if not mp3_path.exists():
            print(f"synth {index + 1}/{len(SENTENCES)}")
            asyncio.run(synth_sentence(text, mp3_path))

    durations: list[float] = []
    for mp3_path, wav_path in zip(mp3_paths, wav_paths):
        if not wav_path.exists():
            durations.append(mp3_to_wav(mp3_path, wav_path))
        else:
            with wave.open(str(wav_path), "rb") as wav_file:
                durations.append(wav_file.getnframes() / wav_file.getframerate())

    full_path = Path(r"D:\codex\LLA\data\materials\preset-001.wav")
    if not full_path.exists():
        concat_wavs(wav_paths, full_path)

    timestamped: list[dict[str, object]] = []
    cursor = 0.0
    for text, duration in zip(SENTENCES, durations):
        start = round(cursor, 3)
        end = round(cursor + duration, 3)
        timestamped.append({"text": text, "start_time": start, "end_time": end})
        cursor = end + SILENCE_BETWEEN

    payload = {
        "material_id": "preset-001",
        "title": "The Quiet Power of Rivers (slow, clear)",
        "audio_path": "data/materials/preset-001.wav",
        "transcript": " ".join(SENTENCES),
        "timestamped_sentences": timestamped,
    }
    manifest = OUT_DIR / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"total duration: {cursor - SILENCE_BETWEEN:.1f}s over {len(SENTENCES)} sentences")
    print(f"manifest: {manifest}")


if __name__ == "__main__":
    main()
