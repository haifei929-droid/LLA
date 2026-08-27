from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from difflib import SequenceMatcher


class DictationErrorType(StrEnum):
    MISS = "MISS"
    MISHEARD = "MISHEARD"
    WORD_FORM = "WORD_FORM"
    SPELLING = "SPELLING"
    ACTIVE_BLANK = "ACTIVE_BLANK"


@dataclass(frozen=True)
class DictationError:
    error_type: DictationErrorType
    expected: str
    actual: str
    expected_index: int | None
    actual_index: int | None


@dataclass(frozen=True)
class DictationResult:
    expected_text: str
    actual_text: str
    normalized_expected: str
    normalized_actual: str
    is_exact_match: bool
    errors: tuple[DictationError, ...]


_EQUIVALENTS = {
    "would've": "would have",
    "i'm": "i am",
    "don't": "do not",
}

_IRREGULAR_FORMS = {
    form: "be" for form in ("am", "is", "are", "was", "were", "been", "being")
} | {
    form: "go" for form in ("go", "goes", "went", "gone", "going")
} | {
    form: "do" for form in ("do", "does", "did", "done", "doing")
} | {
    form: "have" for form in ("have", "has", "had", "having")
} | {
    form: "take" for form in ("take", "takes", "took", "taken", "taking")
} | {
    form: "write" for form in ("write", "writes", "wrote", "written", "writing")
}


def normalize_for_match(text: str) -> str:
    """Normalize only defined writing equivalents and whitespace/punctuation."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = normalized.replace("’", "'").replace("‘", "'")
    for source, target in _EQUIVALENTS.items():
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized)
    normalized = re.sub(r"[^\w']+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _tokens(text: str) -> list[str]:
    return normalize_for_match(text).split()


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _same_word_family(expected: str, actual: str) -> bool:
    if _stem(expected) == _stem(actual):
        return True
    return bool(_IRREGULAR_FORMS.get(expected)) and _IRREGULAR_FORMS.get(expected) == _IRREGULAR_FORMS.get(actual)


def _classify(expected: str, actual: str) -> DictationErrorType:
    if not actual or actual in {"____", "___", "…"}:
        return DictationErrorType.ACTIVE_BLANK
    if _same_word_family(expected, actual) and expected != actual:
        return DictationErrorType.WORD_FORM
    distance = int(round((1 - SequenceMatcher(None, expected, actual).ratio()) * max(len(expected), len(actual))))
    if expected[:1] == actual[:1] and distance <= (1 if max(len(expected), len(actual)) <= 5 else 2):
        return DictationErrorType.SPELLING
    return DictationErrorType.MISHEARD


def evaluate_dictation(expected_text: str, actual_text: str) -> DictationResult:
    expected_tokens = _tokens(expected_text)
    actual_tokens = _tokens(actual_text)
    normalized_expected = " ".join(expected_tokens)
    normalized_actual = " ".join(actual_tokens)
    errors: list[DictationError] = []
    matcher = SequenceMatcher(a=expected_tokens, b=actual_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        expected_slice = expected_tokens[i1:i2]
        actual_slice = actual_tokens[j1:j2]
        pairs = max(len(expected_slice), len(actual_slice))
        for offset in range(pairs):
            expected = expected_slice[offset] if offset < len(expected_slice) else ""
            actual = actual_slice[offset] if offset < len(actual_slice) else ""
            error_type = DictationErrorType.MISS if not actual else _classify(expected, actual)
            if actual in {"____", "___", "…"}:
                error_type = DictationErrorType.ACTIVE_BLANK
            errors.append(
                DictationError(
                    error_type=error_type,
                    expected=expected,
                    actual=actual,
                    expected_index=i1 + offset if expected else None,
                    actual_index=j1 + offset if actual else None,
                )
            )
    return DictationResult(
        expected_text=expected_text,
        actual_text=actual_text,
        normalized_expected=normalized_expected,
        normalized_actual=normalized_actual,
        is_exact_match=not errors and normalized_expected == normalized_actual,
        errors=tuple(errors),
    )
