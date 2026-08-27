"""Audio quality grading (P1): Clear / Acceptable / Poor with audit evidence.

Extends the P0 quality gate with a three-level grading and a versioned
audit record. Thresholds live in this module (versioned via
THRESHOLD_CONFIG_VERSION) so Provider / UI / prepare logic never carries its
own quality numbers. P0 callers keep using `passed` (Clear and Acceptable
both pass; Poor is rejected).
"""

from __future__ import annotations

import array
import hashlib
import math
import wave
from dataclasses import dataclass

ANALYZER_VERSION = "1.0"
THRESHOLD_CONFIG_VERSION = "1.0"


@dataclass(frozen=True)
class QualityThresholds:
    #: SNR (speech vs noise floor) floors per level, in dB.
    clear_min_snr_db: float = 20.0
    acceptable_min_snr_db: float = 12.0
    #: Max silence ratio (P1: keep clear speech dominant).
    max_silence_ratio: float = 0.55
    #: Min duration so a truncated download never counts as clear.
    min_duration_seconds: float = 60.0
    min_sample_rate: int = 16000
    #: Clipping ratio above which the signal is considered distorted.
    max_clipping_ratio: float = 0.001
    #: Loudness band (normalized RMS) outside which volume is questionable.
    min_rms: float = 0.02
    max_rms: float = 0.85


@dataclass(frozen=True)
class AudioQuality:
    level: str  # Clear | Acceptable | Poor
    sample_rate: int
    duration_seconds: float
    snr_db: float
    silence_ratio: float
    rms_mean: float
    clipping_ratio: float
    fingerprint: str
    analyzer_version: str = ANALYZER_VERSION
    threshold_config_version: str = THRESHOLD_CONFIG_VERSION
    failure_code: str | None = None

    @property
    def passed(self) -> bool:
        # P0-compatible: Poor is the only failing level.
        return self.level != "Poor"


class AudioQualityAnalyzer:
    """Energy/statistics-based three-level quality probe."""

    def __init__(self, thresholds: QualityThresholds | None = None) -> None:
        self.thresholds = thresholds or QualityThresholds()

    def analyze(self, audio_path: str) -> AudioQuality:
        try:
            with wave.open(audio_path, "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                frame_width = wav_file.getsampwidth()
                if frame_width != 2:
                    raise ValueError("AudioQualityAnalyzer only supports 16-bit PCM")
                window_frames = max(1, sample_rate // 50)  # 20 ms windows
                speech_rms: list[float] = []
                silence_rms: list[float] = []
                total_samples = 0
                clipped_samples = 0
                while True:
                    frames = wav_file.readframes(window_frames)
                    if not frames:
                        break
                    samples = array.array("h", frames)
                    if channels > 1:
                        samples = samples[::channels]
                    total_samples += len(samples)
                    clipped_samples += sum(1 for sample in samples if abs(sample) >= 32767)
                    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
                    (speech_rms if rms > 0.01 else silence_rms).append(rms)
                total_frames = wav_file.getnframes()
        except (OSError, wave.Error) as exc:
            return AudioQuality(
                level="Poor",
                sample_rate=0,
                duration_seconds=0.0,
                snr_db=0.0,
                silence_ratio=1.0,
                rms_mean=0.0,
                clipping_ratio=1.0,
                fingerprint=_fingerprint_of_path(audio_path),
                failure_code="AUDIO_READ_FAILED",
            )

        duration = total_frames / sample_rate
        snr_db = _snr_db(speech_rms, silence_rms)
        silence_ratio = len(silence_rms) / max(1, len(speech_rms) + len(silence_rms))
        rms_mean = sum(speech_rms) / max(1, len(speech_rms)) if speech_rms else 0.0
        clipping_ratio = clipped_samples / max(1, total_samples)

        t = self.thresholds
        level = "Poor"
        if (
            sample_rate >= t.min_sample_rate
            and duration >= t.min_duration_seconds
            and silence_ratio <= t.max_silence_ratio
            and clipping_ratio <= t.max_clipping_ratio
            and t.min_rms <= rms_mean <= t.max_rms
        ):
            level = "Clear" if snr_db >= t.clear_min_snr_db else "Acceptable"
        elif snr_db < t.acceptable_min_snr_db:
            level = "Poor"

        return AudioQuality(
            level=level,
            sample_rate=sample_rate,
            duration_seconds=duration,
            snr_db=snr_db,
            silence_ratio=silence_ratio,
            rms_mean=rms_mean,
            clipping_ratio=clipping_ratio,
            fingerprint=_fingerprint_of_path(audio_path),
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


def _fingerprint_of_path(audio_path: str) -> str:
    """Content-independent fingerprint of the file identity for audit."""
    from pathlib import Path

    try:
        size = Path(audio_path).stat().st_size
        with open(audio_path, "rb") as f:
            head = f.read(65536)
            f.seek(max(0, size - 65536))
            tail = f.read(65536)
        return hashlib.sha256(head + tail).hexdigest()[:32]
    except OSError:
        return hashlib.sha256(audio_path.encode()).hexdigest()[:32]
