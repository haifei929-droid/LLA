"""Deterministic audio analysis for reading scoring (Spec 25, M3).

Measurement and interpretation are separated: this module only produces facts
(duration, pauses, energy variance) from a 16-bit PCM WAV file. PASS/CLOSE/FAIL
decisions live in the Rule Engine (core/reading_scoring.py), and the LLM only
explains results. The stress dimension is a documented simplification proxy:
energy-variance comparison, pending calibration with real recordings.
"""

from __future__ import annotations

import array
import math
import wave
from dataclasses import dataclass
from typing import Protocol

#: 16-bit PCM maps to [-1, 1] when divided by this value.
_PCM_SCALE = 32768.0


@dataclass(frozen=True)
class Pause:
    start: float
    end: float
    duration: float


@dataclass(frozen=True)
class AudioFeatures:
    duration_seconds: float
    sample_rate: int
    pauses: tuple[Pause, ...]
    #: Coefficient of variation of window RMS; a flat delivery has low
    #: variance, a stressed delivery has high variance. Simplified stress
    #: proxy, not a phonetic measurement.
    rms_cv: float


class AudioAnalyzer(Protocol):
    def analyze(self, audio_path: str) -> AudioFeatures:
        """Return deterministic acoustic facts for a WAV file."""


class WaveAudioAnalyzer:
    """Pure-standard-library analyzer for mono/stereo 16-bit PCM WAV files."""

    def __init__(
        self,
        window_ms: float = 20.0,
        silence_threshold: float = 0.02,
        min_pause_ms: float = 500.0,
    ) -> None:
        # 500 ms minimum pause (calibrated on real speech, Spec 33): word-internal
        # micro-pauses no longer count as pauses; breath-group pauses do.
        self.window_ms = window_ms
        self.silence_threshold = silence_threshold
        self.min_pause_ms = min_pause_ms

    def analyze(self, audio_path: str) -> AudioFeatures:
        with wave.open(audio_path, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            frame_width = wav_file.getsampwidth()
            if frame_width != 2:
                raise ValueError("WaveAudioAnalyzer only supports 16-bit PCM WAV files")
            window_frames = max(1, int(sample_rate * self.window_ms / 1000))
            rms_values: list[float] = []
            while True:
                frames = wav_file.readframes(window_frames)
                if not frames:
                    break
                samples = array.array("h", frames)
                if channels > 1:
                    samples = samples[::channels]
                mean_square = sum(sample * sample for sample in samples) / len(samples)
                rms_values.append(math.sqrt(mean_square) / _PCM_SCALE)
            total_frames = wav_file.getnframes()

        duration = total_frames / sample_rate
        pauses = self._extract_pauses(rms_values, duration)
        rms_cv = self._rms_coefficient_of_variation(rms_values)
        return AudioFeatures(
            duration_seconds=duration,
            sample_rate=sample_rate,
            pauses=pauses,
            rms_cv=rms_cv,
        )

    def _extract_pauses(self, rms_values: list[float], duration: float) -> tuple[Pause, ...]:
        window_seconds = self.window_ms / 1000.0
        pauses: list[Pause] = []
        silence_start: float | None = None
        for index, rms in enumerate(rms_values):
            silent = rms < self.silence_threshold
            if silent and silence_start is None:
                silence_start = index * window_seconds
            elif not silent and silence_start is not None:
                end = index * window_seconds
                if end - silence_start >= self.min_pause_ms / 1000.0:
                    pauses.append(Pause(start=silence_start, end=end, duration=end - silence_start))
                silence_start = None
        if silence_start is not None:
            end = duration
            if end - silence_start >= self.min_pause_ms / 1000.0:
                pauses.append(Pause(start=silence_start, end=end, duration=end - silence_start))
        return tuple(pauses)

    @staticmethod
    def _rms_coefficient_of_variation(rms_values: list[float]) -> float:
        if not rms_values:
            return 0.0
        mean = sum(rms_values) / len(rms_values)
        if mean <= 0:
            return 0.0
        variance = sum((value - mean) ** 2 for value in rms_values) / len(rms_values)
        return math.sqrt(variance) / mean
