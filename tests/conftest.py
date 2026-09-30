"""Shared fixtures: fake providers and fake OpenAI-style clients."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from russian_llm_pack.core.config import RouterConfig
from russian_llm_pack.core.router import Router
from russian_llm_pack.types import (
    ProviderAuthError,
    ProviderRequestError,
    ProviderTransientError,
)


def fake_openai_response(text: str = "ok", prompt_tokens: int = 5, completion_tokens: int = 7):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class FakeClient:
    """Mimics openai.OpenAI's `.chat.completions.create` surface."""

    def __init__(self, text: str = "fake reply", exc: Exception | None = None):
        self._text = text
        self._exc = exc
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return fake_openai_response(self._text)


class FakeProvider:
    """Scripted LLMPort implementation for router tests."""

    name = "fake"

    def __init__(self, name: str = "fake", script=None):
        self.name = name
        self.script = script or []  # list of: "ok" | Exception subclass instance
        self.calls = 0

    def complete(self, messages, *, model=None, **kwargs):
        from russian_llm_pack.types import CompletionResult, UsageInfo

        step = self.script[self.calls] if self.calls < len(self.script) else "ok"
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        text = step if isinstance(step, str) and step != "ok" else f"{self.name}:{model}:ok"
        return CompletionResult(
            text=text,
            provider=self.name,
            model=model or "default",
            usage=UsageInfo(input_tokens=1, output_tokens=2, total_tokens=3),
            latency_ms=1.0,
        )

    def stream(self, messages, *, model=None, **kwargs):
        from russian_llm_pack.types import StreamEvent

        yield StreamEvent(delta="he", provider=self.name, model=model or "default")
        yield StreamEvent(delta="llo", provider=self.name, model=model or "default")


@pytest.fixture
def simple_config() -> RouterConfig:
    return RouterConfig()


@pytest.fixture
def router_factory(simple_config):
    def make(providers: dict, events=None, config: RouterConfig | None = None):
        return Router(config=config or simple_config, providers=providers, on_event=events)

    return make


__all__ = [
    "FakeClient",
    "FakeProvider",
    "fake_openai_response",
    "ProviderAuthError",
    "ProviderRequestError",
    "ProviderTransientError",
]
