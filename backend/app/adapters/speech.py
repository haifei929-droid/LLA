from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RecognizedSegment:
    text: str
    start_time: float
    end_time: float


class SpeechRecognitionProvider(Protocol):
    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        """Return timestamped speech segments for an audio file."""


class LocalSpeechRecognitionProvider:
    """Placeholder for a local ASR implementation with optional phoneme output."""

    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        raise NotImplementedError(f"No local ASR provider is configured for {audio_path}")


class WhisperASRProvider:
    """Local ASR via faster-whisper (Spec 24.1: timestamps only; the official
    transcript remains the standard answer). The model loads lazily."""

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio_path: str) -> list[RecognizedSegment]:
        model = self._ensure_model()
        segments, _info = model.transcribe(audio_path, language="en")
        return [
            RecognizedSegment(text=segment.text.strip(), start_time=segment.start, end_time=segment.end)
            for segment in segments
            if segment.text.strip()
        ]

