"""Generic OpenAI-compatible adapter — the workhorse.

Most sanction-friendly providers (DeepSeek, Z.ai, and partially GigaChat)
speak the OpenAI `/chat/completions` dialect. One engine covers them all:
subclasses only carry a base_url, an env var name and a model list.

Error mapping (the router reasons about these):
    AuthenticationError        -> ProviderAuthError      (skip provider, no retry)
    RateLimit / conn / 5xx    -> ProviderTransientError (retry, then next model)
    other APIError / 4xx       -> ProviderRequestError   (next model, no retry)
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Iterator, Optional

from ..types import (
    ChatMessage,
    CompletionResult,
    ProviderAuthError,
    ProviderError,
    ProviderRequestError,
    ProviderTransientError,
    StreamEvent,
    UsageInfo,
    coerce_messages,
)


class OpenAICompatibleAdapter:
    """Adapter for any OpenAI-compatible chat completions endpoint.

    Args:
        name: provider id used in routing ("deepseek", "zai", ...)
        base_url: provider API root (without /chat/completions).
        api_key: secret. Comes from env — callers must not hardcode it.
        default_model: used when `model` is not passed explicitly.
        timeout: request timeout in seconds.
        client: injectable OpenAI-compatible client object (tests / custom
            transports). When None, a real `openai.OpenAI` client is created.
        extra_headers: optional default headers (e.g. x-folder-id).
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        default_model: Optional[str] = None,
        timeout: float = 60.0,
        client: Any = None,
        extra_headers: Optional[dict] = None,
    ) -> None:
        if client is None:
            from openai import OpenAI  # lazy import: keeps tests light

            client = OpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_retries=0,  # retry policy belongs to the Router, not the SDK
                default_headers=extra_headers or None,
            )
        self.name = name
        self.base_url = base_url
        self.default_model = default_model
        self._client = client

    # -- LLMPort implementation --------------------------------------------

    def complete(
        self,
        messages: Iterable[ChatMessage | str | dict] | ChatMessage | str,
        *,
        model: Optional[str] = None,
        **kwargs,
    ) -> CompletionResult:
        msgs = coerce_messages(messages)
        model = model or self.default_model
        if not model:
            raise ProviderRequestError(
                f"[{self.name}] no model given and adapter has no default_model"
            )
        payload = [m.to_dict() for m in msgs]
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=model, messages=payload, **kwargs
            )
        except Exception as exc:  # noqa: BLE001 — single mapping point
            raise self._wrap_error(exc, model) from exc
        latency_ms = (time.monotonic() - started) * 1000.0

        choice = response.choices[0]
        text = getattr(choice.message, "content", None) or ""
        finish_reason = getattr(choice, "finish_reason", None)
        usage = UsageInfo.from_openai(getattr(response, "usage", None))
        return CompletionResult(
            text=text,
            provider=self.name,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            raw=response,
        )

    def stream(
        self,
        messages: Iterable[ChatMessage | str | dict] | ChatMessage | str,
        *,
        model: Optional[str] = None,
        **kwargs,
    ) -> Iterator[StreamEvent]:
        msgs = coerce_messages(messages)
        model = model or self.default_model
        if not model:
            raise ProviderRequestError(
                f"[{self.name}] no model given and adapter has no default_model"
            )
        payload = [m.to_dict() for m in msgs]
        try:
            stream_obj = self._client.chat.completions.create(
                model=model, messages=payload, stream=True, **kwargs
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc, model) from exc

        for chunk in stream_obj:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            yield StreamEvent(
                delta=getattr(delta, "content", None) or "",
                provider=self.name,
                model=model,
                finish_reason=getattr(choices[0], "finish_reason", None),
            )

    # -- error mapping -------------------------------------------------------

    def _wrap_error(self, exc: Exception, model: Optional[str]) -> ProviderError:
        """Classify an SDK exception into the router-friendly hierarchy."""
        where = f"[{self.name}/{model or self.default_model}]"
        try:
            import openai
        except ImportError:  # pragma: no cover — openai is a hard dependency
            return ProviderRequestError(f"{where} {type(exc).__name__}: {exc}")

        if isinstance(exc, openai.AuthenticationError):
            return ProviderAuthError(f"{where} authentication failed (check API key env var)")
        if isinstance(exc, (openai.RateLimitError, openai.APIConnectionError)):
            return ProviderTransientError(f"{where} {type(exc).__name__}: {exc}")
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", 0) or 0
            if status >= 500:
                return ProviderTransientError(f"{where} HTTP {status}: {exc}")
            return ProviderRequestError(f"{where} HTTP {status}: {exc}")
        if isinstance(exc, openai.APIError):
            return ProviderRequestError(f"{where} {type(exc).__name__}: {exc}")
        return ProviderRequestError(f"{where} unexpected {type(exc).__name__}: {exc}")

    def __repr__(self) -> str:  # pragma: no cover — debugging nicety
        return (
            f"OpenAICompatibleAdapter(name={self.name!r}, "
            f"base_url={self.base_url!r}, default_model={self.default_model!r})"
        )
