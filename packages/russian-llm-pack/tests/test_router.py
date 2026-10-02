"""Router unit tests: fallback order, retry policy, skipping, events."""

from __future__ import annotations

import pytest

from russian_llm_pack.core.config import RouterConfig
from russian_llm_pack.core.router import Router
from russian_llm_pack.types import (
    NoAvailableModelError,
    ProviderAuthError,
    ProviderRequestError,
    ProviderTransientError,
)
from conftest import FakeProvider


def make_router(providers, task="coding", chain=None, retries=2, events=None):
    config = RouterConfig.from_mapping({
        "routing": {"tasks": {task: chain or ["a/m1", "b/m2", "c/m3"]}},
        "defaults": {"retries": retries, "task": task},
    })
    return Router(config=config, providers=providers, on_event=events)


class TestHappyPath:
    def test_first_model_wins(self):
        a, b = FakeProvider("a"), FakeProvider("b")
        router = make_router({"a": a, "b": b})
        result = router.complete("coding", "hi")
        assert result.provider == "a"
        assert a.calls == 1
        assert b.calls == 0

    def test_task_params_merged(self):
        provider = FakeProvider("a")
        config = RouterConfig.from_mapping({
            "routing": {"tasks": {"coding": ["a/m1"]}, "params": {"coding": {"temperature": 0.7}}},
        })
        router = Router(config=config, providers={"a": provider})
        router.complete("coding", "hi", max_tokens=99)
        assert provider.calls == 1  # FakeProvider swallows kwargs; smoke only

    def test_stream_first_available(self):
        a, b = FakeProvider("a"), FakeProvider("b")
        router = make_router({"a": a, "b": b})
        chunks = list(router.stream("coding", "hi"))
        assert chunks[0].provider == "a"
        assert "".join(c.delta for c in chunks) == "hello"


class TestFallback:
    def test_transient_error_falls_through_after_retries(self):
        a = FakeProvider("a", script=[
            ProviderTransientError("boom"), ProviderTransientError("boom"),
            ProviderTransientError("boom"),
        ])
        b = FakeProvider("b")
        router = make_router({"a": a, "b": b}, retries=2)
        result = router.complete("coding", "hi")
        assert result.provider == "b"
        assert a.calls == 3  # initial + 2 retries

    def test_transient_error_recovered_on_retry(self):
        a = FakeProvider("a", script=[ProviderTransientError("hiccup"), "ok-after-retry"])
        b = FakeProvider("b")
        router = make_router({"a": a, "b": b}, retries=2)
        result = router.complete("coding", "hi")
        assert result.provider == "a"
        assert b.calls == 0

    def test_auth_error_skips_provider_immediately(self):
        a = FakeProvider("a", script=[ProviderAuthError("bad key")])
        b = FakeProvider("b")
        router = make_router({"a": a, "b": b}, retries=2)
        result = router.complete("coding", "hi")
        assert result.provider == "b"
        assert a.calls == 1  # no retries on auth errors

    def test_request_error_moves_to_next_model(self):
        a = FakeProvider("a", script=[ProviderRequestError("HTTP 400: bad model")])
        b = FakeProvider("b")
        router = make_router({"a": a, "b": b}, retries=2)
        result = router.complete("coding", "hi")
        assert result.provider == "b"
        assert a.calls == 1

    def test_unconfigured_provider_is_skipped(self):
        b = FakeProvider("b")
        router = make_router({"b": b}, chain=["ghost/m0", "b/m2"])
        result = router.complete("coding", "hi")
        assert result.provider == "b"

    def test_all_chain_exhausted_raises(self):
        a = FakeProvider("a", script=[
            ProviderTransientError("x"), ProviderTransientError("x"),
            ProviderTransientError("x"),
        ])
        router = make_router({"a": a}, chain=["a/m1"], retries=2)
        with pytest.raises(NoAvailableModelError) as excinfo:
            router.complete("coding", "hi")
        assert "a/m1" in str(excinfo.value)

    def test_no_providers_at_all(self):
        router = make_router({}, chain=["a/m1", "b/m2"])
        with pytest.raises(NoAvailableModelError):
            router.complete("coding", "hi")


class TestDirectModel:
    def test_direct_call_bypasses_routing(self):
        a = FakeProvider("a")
        router = make_router({"a": a})
        result = router.complete(None, "hi", model="a/m1")
        assert result.provider == "a"
        assert result.model == "m1"

    def test_direct_call_unknown_provider_raises(self):
        router = make_router({})
        with pytest.raises(NoAvailableModelError):
            router.complete(None, "hi", model="ghost/m1")


class TestEvents:
    def test_skip_and_ok_events_emitted(self):
        events = []
        b = FakeProvider("b")
        router = make_router({"b": b}, chain=["ghost/m0", "b/m2"], events=events.append)
        router.complete("coding", "hi")
        kinds = [e["event"] for e in events]
        assert kinds == ["skip", "ok"]

    def test_event_callback_errors_are_swallowed(self):
        def broken(_event):
            raise RuntimeError("observer bug must not break routing")

        a = FakeProvider("a")
        router = make_router({"a": a}, events=broken)
        result = router.complete("coding", "hi")
        assert result.provider == "a"


class TestStatus:
    def test_status_includes_keyless_providers(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        config = RouterConfig.builtin()
        router = Router.from_config(config)
        names = [info["name"] for info in router.status()]
        assert set(names) >= {"deepseek", "zai", "gigachat", "yandexgpt"}
        assert all(not info["built"] for info in router.status())
