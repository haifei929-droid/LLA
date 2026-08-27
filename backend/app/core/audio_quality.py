"""Audio quality assessment for material search (user requirement: 清晰音质).

Deterministic measurements on the downloaded WAV: sample rate, SNR estimate
(speech vs silence RMS), silence ratio, and duration. Thresholds are Settings
fields so the bar can be tuned per deployment.
"""

from __future__ import annotations

import array
import math
import wave
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioQuality:
    sample_rate: int
    duration_seconds: float
    snr_db: float
    silence_ratio: float
    passed: bool


class AudioQualityAnalyzer:
    """Energy-based quality probe; a clearly-speechy signal passes."""

    def __init__(
        self,
        min_sample_rate: int = 16000,
        min_snr_db: float = 15.0,
        max_silence_ratio: float = 0.55,
        min_duration_seconds: float = 60.0,
    ) -> None:
        self.min_sample_rate = min_sample_rate
        self.min_snr_db = min_snr_db
        self.max_silence_ratio = max_silence_ratio
        self.min_duration_seconds = min_duration_seconds

    def analyze(self, audio_path: str) -> AudioQuality:
        with wave.open(audio_path, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            if wav_file.getsampwidth() != 2:
                raise ValueError("AudioQualityAnalyzer only supports 16-bit PCM")
            window_frames = max(1, sample_rate // 50)  # 20 ms windows
            speech_rms: list[float] = []
            silence_rms: list[float] = []
            while True:
                frames = wav_file.readframes(window_frames)
                if not frames:
                    break
                samples = array.array("h", frames)
                if wav_file.getnchannels() > 1:
                    samples = samples[:: wav_file.getnchannels()]
                rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
                (speech_rms if rms > 0.01 else silence_rms).append(rms)
            total_frames = wav_file.getnframes()

        duration = total_frames / sample_rate
        snr_db = _snr_db(speech_rms, silence_rms)
        silence_ratio = len(silence_rms) / max(1, len(speech_rms) + len(silence_rms))
        passed = (
            sample_rate >= self.min_sample_rate
            and duration >= self.min_duration_seconds
            and snr_db >= self.min_snr_db
            and silence_ratio <= self.max_silence_ratio
        )
        return AudioQuality(
            sample_rate=sample_rate,
            duration_seconds=duration,
            snr_db=snr_db,
            silence_ratio=silence_ratio,
            passed=passed,
        )


def _snr_db(speech_rms: list[float], silence_rms: list[float]) -> float:
    if not speech_rms:
        return 0.0
    if not silence_rms:
        # No silence at all means no measurable noise floor: treat as clean.
        return 40.0
    speech = sum(speech_rms) / len(speech_rms)
    silence = sum(silence_rms) / len(silence_rms)
    if silence <= 1e-6:
        return 40.0
    return 20 * math.log10(max(speech, 1e-6) / silence)
