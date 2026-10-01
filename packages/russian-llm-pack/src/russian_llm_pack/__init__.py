"""russian-llm-pack (RLP).

Unified LLM port for sanction-friendly providers (DeepSeek, Z.ai, GigaChat,
YandexGPT) with YAML-driven routing and fallback chains.

Architecture (hexagonal):
    ports/      -- stable interfaces the harness depends on (LLMPort)
    providers/  -- replaceable adapters (OpenAI-compatible engine + presets)
    core/       -- domain logic: config resolution, routing, fallback
    cli.py      -- thin CLI (`rlp`) for manual checks and dogfooding

Security rule: API keys live in environment variables only. Never in YAML,
never in code, never in commits.
"""

from .core.config import RouterConfig, load_config
from .core.router import Router
from .types import (
    ChatMessage,
    CompletionResult,
    ConfigError,
    ModelRef,
    NoAvailableModelError,
    ProviderAuthError,
    ProviderError,
    ProviderRequestError,
    ProviderTransientError,
    RLLError,
    StreamEvent,
    UsageInfo,
)

__version__ = "0.3.0"

__all__ = [
    "ChatMessage",
    "CompletionResult",
    "ConfigError",
    "ModelRef",
    "NoAvailableModelError",
    "ProviderAuthError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderTransientError",
    "RLLError",
    "Router",
    "RouterConfig",
    "StreamEvent",
    "UsageInfo",
    "__version__",
    "load_config",
]
