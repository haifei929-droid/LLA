from __future__ import annotations

from typing import Protocol


class LLMAdapter(Protocol):
    def complete(self, prompt: str) -> str:
        """Generate a response for tasks that are not deterministic domain rules."""


class UnavailableLLMAdapter:
    """Explicit fallback so core training logic never silently depends on an LLM."""

    def complete(self, prompt: str) -> str:
        raise NotImplementedError("No LLM adapter is configured")

