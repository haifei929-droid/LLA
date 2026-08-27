from __future__ import annotations

import re
from dataclasses import dataclass


class MaterialPreprocessError(ValueError):
    pass


@dataclass(frozen=True)
class TimestampedSentence:
    text: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass(frozen=True)
class SentenceSpec:
    sentence_id: str
    material_id: str
    part_no: int
    sequence_no: int
    text: str
    normalized_text: str
    start_time: float
    end_time: float


@dataclass(frozen=True)
class MaterialSpec:
    material_id: str
    title: str
    audio_path: str
    transcript: str
    duration_seconds: float
    sentences: tuple[SentenceSpec, ...]
    part_boundaries: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    speech_rate_wpm: float
    status: str = "READY"


def normalize_sentence(text: str) -> str:
    return " ".join(text.lower().split())


class MaterialPreprocessor:
    """Build immutable training units from pre-timestamped preset material."""

    def process(
        self,
        *,
        material_id: str,
        title: str,
        audio_path: str,
        transcript: str,
        timestamped_sentences: list[TimestampedSentence],
        natural_part_boundaries: list[int] | None = None,
    ) -> MaterialSpec:
        self._validate(material_id, title, audio_path, transcript, timestamped_sentences)
        duration_seconds = timestamped_sentences[-1].end_time
        part_numbers = self._assign_parts(
            timestamped_sentences, duration_seconds, natural_part_boundaries
        )
        sentences = tuple(
            SentenceSpec(
                sentence_id=f"{material_id}-sentence-{index:03d}",
                material_id=material_id,
                part_no=part_numbers[index - 1],
                sequence_no=index,
                text=sentence.text.strip(),
                normalized_text=normalize_sentence(sentence.text),
                start_time=sentence.start_time,
                end_time=sentence.end_time,
            )
            for index, sentence in enumerate(timestamped_sentences, start=1)
        )
        boundaries = tuple(
            (
                next(sentence.start_time for sentence, part in zip(timestamped_sentences, part_numbers) if part == number),
                next(sentence.end_time for sentence, part in reversed(list(zip(timestamped_sentences, part_numbers))) if part == number),
            )
            for number in (1, 2, 3)
        )
        words = len(re.findall(r"\b[\w']+\b", transcript))
        speech_rate_wpm = words / (duration_seconds / 60)
        return MaterialSpec(
            material_id=material_id,
            title=title,
            audio_path=audio_path,
            transcript=transcript,
            duration_seconds=duration_seconds,
            sentences=sentences,
            part_boundaries=boundaries,
            speech_rate_wpm=round(speech_rate_wpm, 2),
        )

    @staticmethod
    def _validate(
        material_id: str,
        title: str,
        audio_path: str,
        transcript: str,
        sentences: list[TimestampedSentence],
    ) -> None:
        if not all((material_id.strip(), title.strip(), audio_path.strip(), transcript.strip())):
            raise MaterialPreprocessError("material_id, title, audio_path, and transcript are required")
        if len(sentences) < 3:
            raise MaterialPreprocessError("At least three timestamped sentences are required for three Parts")
        previous_end = 0.0
        for sentence in sentences:
            if not sentence.text.strip() or sentence.start_time < previous_end or sentence.end_time <= sentence.start_time:
                raise MaterialPreprocessError("Sentence timestamps must be non-empty, ordered, and positive")
            previous_end = sentence.end_time

    @staticmethod
    def _assign_parts(
        sentences: list[TimestampedSentence],
        duration: float,
        natural_boundaries: list[int] | None = None,
    ) -> list[int]:
        """Split into three Parts, preferring complete sentences at natural
        semantic boundaries (Spec 3.2) near the time targets, falling back to
        the closest sentence boundary."""
        targets = (duration / 3, duration * 2 / 3)
        natural = sorted(natural_boundaries or [])
        tolerance = duration * 0.12
        boundaries: list[int] = []
        for target in targets:
            lower = boundaries[-1] + 1 if boundaries else 1
            # Candidate sentence indexes are 1..len(sentences)-1; the upper
            # bound stays open (range semantics) so the last sentence is never
            # a boundary but smaller inputs never produce an empty range.
            upper = len(sentences)
            near_natural = [
                index
                for index in natural
                if lower <= index < upper
                and abs(sentences[index - 1].end_time - target) <= tolerance
            ]
            if near_natural:
                candidate = min(
                    near_natural, key=lambda index: abs(sentences[index - 1].end_time - target)
                )
                natural.remove(candidate)
            else:
                candidate = min(
                    range(lower, upper),
                    key=lambda index: abs(sentences[index - 1].end_time - target),
                )
            boundaries.append(candidate)
        return [1 if index <= boundaries[0] else 2 if index <= boundaries[1] else 3 for index in range(1, len(sentences) + 1)]
