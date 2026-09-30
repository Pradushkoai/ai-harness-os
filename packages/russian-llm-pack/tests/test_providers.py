"""Provider adapter unit tests with injected fake clients (no network)."""

from __future__ import annotations

import httpx
import openai
import pytest

from russian_llm_pack.providers.base import OpenAICompatibleAdapter
from russian_llm_pack.providers.registry import PRESETS, build_provider, preset_info
from russian_llm_pack.types import (
    ChatMessage,
    ProviderAuthError,
    ProviderRequestError,
    ProviderTransientError,
)
from conftest import FakeClient


def make_adapter(client, name="deepseek", default_model="deepseek-chat"):
    return OpenAICompatibleAdapter(
        name=name,
        base_url="https://fake.local",
        api_key="test-key",
        default_model=default_model,
        client=client,
    )


class TestComplete:
    def test_maps_fields(self):
        client = FakeClient(text="привет из мока")
        adapter = make_adapter(client)
        result = adapter.complete([ChatMessage.user("hi")])
        assert result.text == "привет из мока"
        assert result.provider == "deepseek"
        assert result.model == "deepseek-chat"
        assert result.finish_reason == "stop"
        assert result.usage.input_tokens == 5
        assert result.usage.output_tokens == 7
        assert result.usage.total_tokens == 12
        assert result.latency_ms >= 0

    def test_string_messages_coerced(self):
        client = FakeClient()
        adapter = make_adapter(client)
        adapter.complete("hi")
        assert client.calls[0]["messages"] == [{"role": "user", "content": "hi"}]

    def test_kwargs_passed_through(self):
        client = FakeClient()
        adapter = make_adapter(client)
        adapter.complete("hi", temperature=0.3, max_tokens=42)
        assert client.calls[0]["temperature"] == 0.3
        assert client.calls[0]["max_tokens"] == 42

    def test_explicit_model_overrides_default(self):
        client = FakeClient()
        adapter = make_adapter(client)
        adapter.complete("hi", model="deepseek-reasoner")
        assert client.calls[0]["model"] == "deepseek-reasoner"


class TestStream:
    def test_stream_deltas(self):
        chunks = [
            _chunk("Hel"),
            _chunk("lo"),
            _chunk(None, finish_reason="stop"),
        ]

        class StreamClient:
            def __init__(self):
                self.chat = type("C", (), {})()

        client = StreamClient()

        def _create(**kwargs):
            assert kwargs["stream"] is True
            return iter(chunks)

        client.chat.completions = type("Completions", (), {"create": staticmethod(_create)})()
        adapter = make_adapter(client)
        events = list(adapter.stream([ChatMessage.user("hi")]))
        assert "".join(e.delta for e in events) == "Hello"
        assert events[-1].finish_reason == "stop"


def _chunk(delta_text, finish_reason=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=delta_text),
                finish_reason=finish_reason,
            )
        ]
    )


class TestErrorMapping:
    def _api_error(self, exc_cls, status=None):
        request = httpx.Request("POST", "https://fake.local/chat/completions")
        if status is not None:
            response = httpx.Response(status, request=request)
            return exc_cls("boom", response=response, body=None)
        return exc_cls("boom", request=request)

    def test_auth_error(self):
        adapter = make_adapter(FakeClient())
        adapter._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            self._api_error(openai.AuthenticationError, 401)
        )
        with pytest.raises(ProviderAuthError):
            adapter.complete("hi")

    def test_rate_limit_is_transient(self):
        adapter = make_adapter(FakeClient())
        adapter._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            self._api_error(openai.RateLimitError, 429)
        )
        with pytest.raises(ProviderTransientError):
            adapter.complete("hi")

    def test_500_is_transient(self):
        adapter = make_adapter(FakeClient())
        adapter._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            self._api_error(openai.InternalServerError, 500)
        )
        with pytest.raises(ProviderTransientError):
            adapter.complete("hi")

    def test_400_is_request_error(self):
        adapter = make_adapter(FakeClient())
        adapter._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            self._api_error(openai.BadRequestError, 400)
        )
        with pytest.raises(ProviderRequestError):
            adapter.complete("hi")

    def test_generic_exception_is_request_error(self):
        adapter = make_adapter(FakeClient())
        adapter._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            ValueError("weird")
        )
        with pytest.raises(ProviderRequestError):
            adapter.complete("hi")


class TestRegistry:
    def test_presets_present(self):
        assert set(PRESETS) == {"deepseek", "zai", "gigachat", "yandexgpt"}

    def test_build_requires_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert build_provider("deepseek") is None

    def test_build_with_env_key(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        adapter = build_provider("deepseek")
        assert adapter is not None
        assert adapter.base_url == "https://api.deepseek.com"
        assert adapter.default_model == "deepseek-chat"

    def test_build_unknown_provider(self):
        assert build_provider("ghost") is None

    def test_native_provider_not_built_yet(self, monkeypatch):
        monkeypatch.setenv("YAIAM_TOKEN", "fake")
        assert build_provider("yandexgpt") is None  # native -> v0.2

    def test_gigachat_builds_with_token(self, monkeypatch):
        monkeypatch.setenv("GIGACHAT_ACCESS_TOKEN", "fake-token")
        adapter = build_provider("gigachat")
        assert adapter is not None
        assert adapter.base_url.startswith("https://gigachat.")

    def test_overrides_respected(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "k")
        adapter = build_provider(
            "deepseek", {"api_key_env": "MY_KEY", "default_model": "deepseek-reasoner"}
        )
        assert adapter is not None
        assert adapter.default_model == "deepseek-reasoner"

    def test_preset_info_reports_key_status(self, monkeypatch):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        info = preset_info("zai")
        assert info["has_key"] is False
        monkeypatch.setenv("ZAI_API_KEY", "k")
        info = preset_info("zai")
        assert info["has_key"] is True
        assert info["default_model"] == "glm-4.6"
