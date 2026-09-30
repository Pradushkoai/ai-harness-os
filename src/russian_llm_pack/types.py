"""Core data types and exceptions for russian-llm-pack.

Deliberately small: messages, results, usage, stream events and the error
hierarchy the router reasons about. Anything heavier (embeddings, tools,
structured output) waits until the harness actually needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RLLError(Exception):
    """Base class for all russian-llm-pack errors."""


class ConfigError(RLLError):
    """Configuration file or environment is invalid."""


class ProviderError(RLLError):
    """A provider call failed."""


class ProviderTransientError(ProviderError):
    """Retryable failure: rate limit, timeout, connection reset, HTTP 5xx."""


class ProviderAuthError(ProviderError):
    """Authentication failed. Never retried — skip the provider entirely."""


class ProviderRequestError(ProviderError):
    """Request rejected (HTTP 4xx, bad model name, malformed payload).
    Retrying the same request will not help; move to the next model."""


class NoAvailableModelError(RLLError):
    """The whole fallback chain for a task is exhausted or unconfigured."""

    def __init__(self, task: str, chain: list[str], errors: list[str]) -> None:
        self.task = task
        self.chain = chain
        self.errors = errors
        detail = "; ".join(errors[-3:]) or "no providers configured"
        super().__init__(
            f"no available model for task '{task}' "
            f"(chain: {chain}; last errors: {detail})"
        )


# ---------------------------------------------------------------------------
# Messages and results
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    """One chat message. Roles: system | user | assistant | tool."""

    role: str
    content: str

    # -- convenience constructors ------------------------------------------
    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role="assistant", content=content)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


def coerce_messages(messages: Any) -> list[ChatMessage]:
    """Normalize user input into a list of ChatMessage.

    Accepts: a single ChatMessage / str, or an iterable of
    ChatMessage / str / {"role": ..., "content": ...} dicts.
    """

    if isinstance(messages, ChatMessage):
        return [messages]
    if isinstance(messages, str):
        return [ChatMessage.user(messages)]
    result: list[ChatMessage] = []
    for item in messages:
        if isinstance(item, ChatMessage):
            result.append(item)
        elif isinstance(item, str):
            result.append(ChatMessage.user(item))
        elif isinstance(item, dict):
            result.append(ChatMessage(role=str(item["role"]), content=str(item.get("content", ""))))
        else:
            raise TypeError(f"unsupported message type: {type(item).__name__}")
    return result


@dataclass
class UsageInfo:
    """Token usage of one completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_openai(cls, usage: Any) -> "UsageInfo":
        """Map an OpenAI-style usage object (or None) onto UsageInfo."""
        if usage is None:
            return cls()
        raw = {k: v for k, v in vars(usage).items()} if hasattr(usage, "__dict__") else {}
        return cls(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            raw=raw,
        )


@dataclass
class CompletionResult:
    """Successful completion from one provider/model."""

    text: str
    provider: str
    model: str
    usage: UsageInfo = field(default_factory=UsageInfo)
    latency_ms: float = 0.0
    finish_reason: Optional[str] = None
    raw: Any = None  # original provider response, for debugging only


@dataclass
class StreamEvent:
    """One streaming delta chunk."""

    delta: str = ""
    provider: str = ""
    model: str = ""
    finish_reason: Optional[str] = None


@dataclass(frozen=True)
class ModelRef:
    """A `provider/model` reference used in routing chains."""

    provider: str
    model: str

    @classmethod
    def parse(cls, ref: str) -> "ModelRef":
        if "/" not in ref:
            raise ConfigError(
                f"invalid model reference '{ref}' — expected 'provider/model' "
                f"(e.g. 'deepseek/deepseek-chat')"
            )
        provider, model = ref.split("/", 1)
        provider, model = provider.strip(), model.strip()
        if not provider or not model:
            raise ConfigError(f"invalid model reference '{ref}' — empty provider or model")
        return cls(provider=provider, model=model)

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"{self.provider}/{self.model}"
