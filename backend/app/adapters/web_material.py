"""WebSearchMaterialProvider: BBC Learning English 6 Minute English.

Spec 24: providers output a standardized MaterialSource; the training core
never cares where material came from. BBC 6 Minute English serves the
transcript and a direct MP3 download on every episode page (server-rendered),
so no headless browser is needed. Rights: BBC content is copyright-protected;
downloading for personal study is standard BBC Learning English usage, and
source attribution is recorded on the material row.
"""

from __future__ import annotations

import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.adapters.speech import RecognizedSegment, SpeechRecognitionProvider

ARCHIVE_URL = "https://www.bbc.co.uk/learningenglish/english/features/6-minute-english"
_USER_AGENT = "Mozilla/5.0 (LanguageTrainingAgent P0; personal study)"


@dataclass(frozen=True)
class MaterialSource:
    material_id: str
    title: str
    audio_path: str
    transcript: str
    source_url: str | None = None
    source_name: str | None = None
    duration_seconds: float | None = None
    #: (text, start_time, end_time) per sentence, aligned by ASR timestamps.
    timestamped_sentences: tuple[tuple[str, float, float], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class WebMaterialProvider(Protocol):
    def search_next(
        self,
        *,
        exclude_urls: set[str],
        work_dir: Path,
        asr: SpeechRecognitionProvider | None = None,
        criteria: object | None = None,
    ) -> MaterialSource:
        """Fetch the next unseen episode and return a timestamped source."""


_ENTITY_MAP = {
    "&rsquo;": "'", "&lsquo;": "'", "&apos;": "'", "&#39;": "'",
    "&ndash;": " ", "&mdash;": " ", "&hellip;": "...", "&nbsp;": " ",
    "&amp;": "&", "&quot;": '"',
}


def split_sentences(text: str) -> list[str]:
    """Split transcript text into sentences on terminal punctuation."""
    parts = re.split(r"(?<=[.!?…])\s+", text.replace("\n", " ").strip())
    return [
        part.strip()
        for part in parts
        if part.strip() and not re.fullmatch(r"[.!?…\s]+", part)
    ]


def align_sentences(sentences: list[str], segments: list[RecognizedSegment]) -> list[tuple[str, float, float]]:
    """Map official transcript sentences onto ASR segment timestamps.

    Full-word-sequence matches become anchors; unmatched sentences are
    distributed between neighboring anchors (or the audio edges) in
    proportion to their word counts. The result is monotonic, stays inside
    the audio duration, and keeps exactly-matched sentences on their true
    positions.
    """
    full_text = " ".join(segment.text for segment in segments)
    raw_words = full_text.split()
    word_ranges: list[tuple[int, int]] = []
    cursor = 0
    for word in raw_words:
        index = full_text.find(word, cursor)
        word_ranges.append((index, index + len(word)))
        cursor = index + len(word)
    # Both sides get the same punctuation cleaning so "english," matches
    # "english" and "I'm" keeps its apostrophe.
    clean_words = [re.sub(r"[^a-z0-9']", "", word.lower()) for word in raw_words]
    positions: list[float] = []
    for segment in segments:
        length = len(segment.text) + (1 if positions else 0)  # + leading space
        span = segment.end_time - segment.start_time
        for offset in range(length):
            positions.append(segment.start_time + offset * span / max(1, len(segment.text)))

    def locate(sentence: str) -> tuple[int, int] | None:
        needle = [re.sub(r"[^a-z0-9']", "", word.lower()) for word in sentence.split()]
        for start in range(len(clean_words) - len(needle) + 1):
            if clean_words[start : start + len(needle)] == needle:
                return start, start + len(needle)
        return None

    def time_of(word_span: tuple[int, int]) -> tuple[float, float]:
        word_start, word_end = word_span
        char_start = word_ranges[word_start][0]
        char_end = word_ranges[word_end - 1][1]
        start = positions[char_start] if char_start < len(positions) else (positions[-1] if positions else 0.0)
        end = positions[char_end - 1] if char_end - 1 < len(positions) else (positions[-1] if positions else 0.0)
        return start, end

    # First pass: collect anchors from exact word-sequence matches, keeping
    # them monotonic (a match landing before the previous anchor is dropped).
    pending: list[tuple[int, str, int]] = []  # (index, sentence, word count)
    result: list[tuple[str, float, float]] = [None] * len(sentences)  # type: ignore[list-item]
    previous_end = 0.0
    for index, sentence in enumerate(sentences):
        word_span = locate(sentence)
        if word_span is not None:
            start, end = time_of(word_span)
            if start >= previous_end - 0.5:
                result[index] = (sentence, start, end)
                previous_end = max(previous_end, end)
                continue
        pending.append((index, sentence, len(sentence.split())))

    # Second pass: interpolate unmatched sentences between their nearest
    # anchors. Sentences sharing the same anchor window are grouped and the
    # window budget is split by word counts; minimum spans keep timestamps
    # strictly ordered even when anchor windows overlap.
    audio_end = segments[-1].end_time if segments else 0.0

    def window_bounds(index: int) -> tuple[float, float]:
        prev_anchor = next(
            (result[i] for i in range(index - 1, -1, -1) if result[i] is not None), None
        )
        next_anchor = next(
            (result[i] for i in range(index + 1, len(result)) if result[i] is not None), None
        )
        start = prev_anchor[2] if prev_anchor else 0.0
        end = next_anchor[1] if next_anchor else audio_end
        return start, end

    groups: dict[tuple[float, float], list[tuple[int, str, int]]] = {}
    for item in pending:
        groups.setdefault(window_bounds(item[0]), []).append(item)
    for (window_start, window_end), items in groups.items():
        total_words = sum(words for _, _, words in items)
        budget = max(0.0, window_end - window_start)
        cursor = window_start
        for index, sentence, words in items:
            span = max(0.15, budget * words / max(1, total_words))
            result[index] = (sentence, cursor, cursor + span)
            cursor += span
    return [item for item in result if item is not None]  # type: ignore[misc]


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def _extract_transcript(html: str) -> str | None:
    marker = html.find(">Transcript<")
    if marker < 0:
        return None
    # Content after the Transcript heading up to the next h1-h4 heading.
    tail = html[marker:]
    next_heading = re.search(r"<h[1-4][^>]*>", tail[20:])
    body = tail[20:] if next_heading is None else tail[20 : 20 + next_heading.start()]
    # Speaker labels like <strong>Dan<br /></strong> carry no punctuation and
    # are not spoken; drop them so dialogue splits on real sentence boundaries.
    body = re.sub(r"<strong>([A-Za-z]+)<br\s*/?></strong>", " ", body)
    text = re.sub(r"<[^>]+>", " ", body)
    for entity, replacement in _ENTITY_MAP.items():
        text = text.replace(entity, replacement)
    return re.sub(r"\s+", " ", text).strip()


class BBCLearningEnglishProvider:
    """6 Minute English episodes: ~6 min, clear intermediate English, MP3 + transcript."""

    def __init__(self, archive_url: str = ARCHIVE_URL) -> None:
        self.archive_url = archive_url

    def _list_episode_urls(self) -> list[str]:
        html = _fetch(self.archive_url)
        links = re.findall(r'href="(/learningenglish/english/features/6-minute-english/ep-[0-9]+)"', html)
        return sorted(set(f"https://www.bbc.co.uk{link}" for link in links))

    def _fetch_episode(self, url: str) -> tuple[str, str, str]:
        html = _fetch(url)
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        mp3 = re.search(r'https?://[^"\'\s<>]+\.mp3', html)
        transcript = _extract_transcript(html)
        if mp3 is None or not transcript:
            raise ValueError(f"Episode has no downloadable transcript/audio: {url}")
        clean_title = re.sub(r"\s+", " ", title.group(1)).strip() if title else url
        return clean_title, transcript, mp3.group(0)

    def search_next(
        self,
        *,
        exclude_urls: set[str],
        work_dir: Path,
        asr: SpeechRecognitionProvider | None = None,
        criteria: object | None = None,
    ) -> MaterialSource:
        # 6 Minute English is a homogeneous source (~6 min, ~150 wpm); the
        # recommender's criteria are recorded by the caller for display and
        # used for source selection once multiple providers exist.
        episodes = [url for url in self._list_episode_urls() if url not in exclude_urls]
        if not episodes:
            raise ValueError("No unseen BBC 6 Minute English episodes available")
        title, transcript, mp3_url = self._fetch_episode(episodes[0])

        episode_id = re.search(r"ep-(\d+)", episodes[0]).group(1)
        material_id = f"web-6me-{episode_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        mp3_path = work_dir / f"{material_id}.mp3"
        wav_path = work_dir / f"{material_id}.wav"
        if not mp3_path.exists():
            urllib.request.urlretrieve(mp3_url, mp3_path)
        if not wav_path.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3_path),
                 "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(wav_path)],
                check=True,
            )

        segments = []
        if asr is not None:
            segments = asr.transcribe(str(wav_path))
        sentences = split_sentences(transcript)
        if segments:
            aligned = align_sentences(sentences, segments)
        else:
            # No ASR: word-proportional approximation over total duration.
            aligned = _approximate_timestamps(sentences, _wav_duration(wav_path))

        return MaterialSource(
            material_id=material_id,
            title=title,
            audio_path=str(wav_path),
            transcript=transcript,
            source_url=episodes[0],
            source_name="BBC Learning English - 6 Minute English",
            duration_seconds=_wav_duration(wav_path),
            timestamped_sentences=tuple(aligned),
        )


def _wav_duration(wav_path: Path) -> float:
    import wave

    with wave.open(str(wav_path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def _approximate_timestamps(sentences: list[str], duration: float) -> list[tuple[str, float, float]]:
    """Fallback timestamps: duration split proportionally to word counts."""
    word_counts = [len(sentence.split()) for sentence in sentences]
    total_words = max(1, sum(word_counts))
    aligned: list[tuple[str, float, float]] = []
    cursor = 0.0
    for sentence, words in zip(sentences, word_counts):
        start = cursor
        end = cursor + duration * words / total_words
        aligned.append((sentence, start, end))
        cursor = end
    return aligned
