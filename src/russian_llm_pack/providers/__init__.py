"""Provider adapters: one generic engine + per-provider presets."""

from .base import OpenAICompatibleAdapter
from .registry import PRESETS, ProviderPreset, build_provider, preset_info

__all__ = [
    "PRESETS",
    "OpenAICompatibleAdapter",
    "ProviderPreset",
    "build_provider",
    "preset_info",
]
