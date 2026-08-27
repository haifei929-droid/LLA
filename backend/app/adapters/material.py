from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MaterialSource:
    material_id: str
    title: str
    audio_path: str
    transcript: str


class MaterialProvider(Protocol):
    def load(self, material_id: str) -> MaterialSource:
        """Load a source material before deterministic preprocessing."""


class PresetMaterialProvider:
    """Placeholder for the local preset-material catalog."""

    def load(self, material_id: str) -> MaterialSource:
        raise NotImplementedError(
            f"No preset material provider is configured for material {material_id}"
        )

