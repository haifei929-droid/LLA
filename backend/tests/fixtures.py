"""Shared test fixtures: material factory, database bootstrap, and synth audio."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from app.config import Settings
from app.core.materials import MaterialStore
from app.db.connection import Database
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence

DEFAULT_SENTENCES = [
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

#: Per-sentence duration in seconds; three Parts of three sentences each.
SENTENCE_DURATION = 4.0


def make_settings(tmp_path: Path, db_name: str = "test.sqlite3") -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=tmp_path / db_name,
        materials_dir=tmp_path / "materials",
        recordings_dir=tmp_path / "recordings",
        processed_dir=tmp_path / "processed",
    )


def make_database(tmp_path: Path, db_name: str = "test.sqlite3") -> Database:
    database = Database(make_settings(tmp_path, db_name))
    database.initialize()
    return database


def timestamped(sentences: list[str] | None = None, duration: float = SENTENCE_DURATION) -> list[TimestampedSentence]:
    texts = sentences or DEFAULT_SENTENCES
    return [
        TimestampedSentence(text, index * duration, (index + 1) * duration)
        for index, text in enumerate(texts)
    ]


def create_material(
    database: Database,
    material_id: str = "m1",
    sentences: list[str] | None = None,
    duration: float = SENTENCE_DURATION,
) -> MaterialStore:
    """Bootstrap a material through the real preprocess + store path."""
    store = MaterialStore(database)
    material = MaterialPreprocessor().process(
        material_id=material_id,
        title="Preset " + material_id,
        audio_path=f"data/materials/{material_id}.wav",
        transcript=" ".join(sentences or DEFAULT_SENTENCES),
        timestamped_sentences=timestamped(sentences, duration),
    )
    store.create(material)
    return store


def sentence_ids(material_id: str, count: int = 9) -> list[str]:
    return [f"{material_id}-sentence-{index:03d}" for index in range(1, count + 1)]


def make_sine_wav(
    path: Path,
    segments: list[tuple[float, float]] | None = None,
    sample_rate: int = 16000,
    frequency: float = 440.0,
) -> Path:
    """Write a deterministic mono 16-bit WAV from (duration, amplitude) segments.

    Amplitude 0 yields silence; segment boundaries let reading-scoring tests
    construct pause patterns and stress-like energy variation without a real
    recording (M3 uses them to assert the three dimensions deterministically).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if segments is None:
        segments = [(2.0, 12000.0)]
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for duration_seconds, amplitude in segments:
            frames = int(duration_seconds * sample_rate)
            for frame in range(frames):
                if amplitude <= 0:
                    sample = 0
                else:
                    sample = int(amplitude * math.sin(2 * math.pi * frequency * frame / sample_rate))
                wav_file.writeframes(struct.pack("<h", sample))
    return path
