"""Unit tests for the native GigaChat adapter — OAuth flow, token cache, TLS policy.

All transport is faked; nothing here touches the network.
"""

from __future__ import annotations

import ssl

import pytest

from russian_llm_pack.providers._http import TransportError
from russian_llm_pack.providers.gigachat import GigaChatAdapter
from russian_llm_pack.types import (
    ProviderAuthError,
    ProviderRequestError,
    ProviderTransientError,
)

BASE = "https://gigachat.devices.sberbank.ru/api/v1"
OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"


def chat_body(text="ответ модели"):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        "model": "GigaChat-Pro",
    }


def oauth_body(token="tok-1", expires_at=9999999999999):
    return {"access_token": token, "expires_at": expires_at}


class FakeTransport:
    """Routes by URL: oauth endpoint -> oauth responder, else chat responder."""

    def __init__(self, oauth_responder=None, chat_responder=None):
        self.calls = []
        self.oauth_responder = oauth_responder or (lambda u, p, h: (200, oauth_body()))
        self.chat_responder = chat_responder or (lambda u, p, h: (200, chat_body()))

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": dict(headers)})
        if "oauth" in url:
            return self.oauth_responder(url, payload, headers)
        return self.chat_responder(url, payload, headers)

    def oauth_calls(self):
        return [c for c in self.calls if "oauth" in c["url"]]

    def chat_calls(self):
        return [c for c in self.calls if "chat/completions" in c["url"]]


def make_adapter(**kwargs):
    transport = kwargs.pop("transport", None) or FakeTransport()
    defaults = dict(
        base_url=BASE,
        oauth_url=OAUTH,
        auth_key="basic-key",
        default_model="GigaChat-Pro",
        transport=transport,
    )
    defaults.update(kwargs)
    adapter = GigaChatAdapter(**defaults)
    return adapter, transport


class TestOAuthFlow:
    def test_oauth_request_shape(self):
        adapter, transport = make_adapter()
        adapter.complete("hi")
        oauth = transport.oauth_calls()[0]
        assert oauth["url"] == OAUTH
        assert oauth["headers"]["Authorization"] == "Basic basic-key"
        assert oauth["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert oauth["payload"] == "scope=GIGACHAT_API_PERS"
        # RqUID must be a fresh uuid per request
        quid = oauth["headers"].get("RqUID", "")
        assert len(quid) == 36 and quid.count("-") == 4

    def test_token_cached_between_calls(self):
        adapter, transport = make_adapter()
        adapter.complete("hi")
        adapter.complete("hi again")
        assert len(transport.oauth_calls()) == 1  # OAuth ran once
        assert len(transport.chat_calls()) == 2
        first = transport.chat_calls()[0]["headers"]["Authorization"]
        second = transport.chat_calls()[1]["headers"]["Authorization"]
        assert first == second == "Bearer tok-1"

    def test_token_refreshed_after_expiry(self):
        expired = oauth_body(token="tok-old", expires_at=1000)  # ancient
        fresh = oauth_body(token="tok-new")
        responses = [expired, fresh]
        transport = FakeTransport(
            oauth_responder=lambda u, p, h: (200, responses.pop(0))
        )
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=transport,
        )
        adapter.complete("hi")
        adapter.complete("hi again")
        assert len(transport.oauth_calls()) == 2  # refresh happened
        bearer = transport.chat_calls()[1]["headers"]["Authorization"]
        assert bearer == "Bearer tok-new"

    def test_refresh_margin_avoids_last_second_expiry(self):
        # token expires in 30s — inside the 60s refresh margin -> refreshed
        import time as _time

        soon = int(_time.time() * 1000) + 30_000
        transport = FakeTransport(
            oauth_responder=lambda u, p, h: (200, oauth_body("tok-soon", soon))
        )
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=transport,
        )
        adapter.complete("hi")
        adapter.complete("hi")
        assert len(transport.oauth_calls()) == 2

    def test_static_token_skips_oauth(self):
        adapter, transport = make_adapter(auth_key=None, access_token="ready-token")
        adapter.complete("hi")
        assert transport.oauth_calls() == []
        headers = transport.chat_calls()[0]["headers"]
        assert headers["Authorization"] == "Bearer ready-token"

    def test_missing_access_token_in_oauth_response(self):
        transport = FakeTransport(oauth_responder=lambda u, p, h: (200, {}))
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=transport,
        )
        with pytest.raises(ProviderAuthError, match="access_token"):
            adapter.complete("hi")

    def test_no_auth_key_and_no_token_raises(self):
        adapter = GigaChatAdapter(base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro")
        with pytest.raises(ProviderAuthError, match="GIGACHAT_AUTH_KEY"):
            adapter.complete("hi")


class TestChatRequest:
    def test_url_and_payload_shape(self):
        adapter, transport = make_adapter()
        adapter.complete(
            [{"role": "system", "content": "ты кодер"}, {"role": "user", "content": "hi"}]
        )
        chat = transport.chat_calls()[0]
        assert chat["url"] == f"{BASE}/chat/completions"
        assert chat["payload"]["model"] == "GigaChat-Pro"
        assert chat["payload"]["messages"] == [
            {"role": "system", "content": "ты кодер"},
            {"role": "user", "content": "hi"},
        ]
        assert chat["headers"]["Accept"] == "application/json"

    def test_kwargs_mapped(self):
        adapter, transport = make_adapter()
        adapter.complete("hi", temperature=0.1, max_tokens=128, top_p=0.9)
        payload = transport.chat_calls()[0]["payload"]
        assert payload["temperature"] == 0.1
        assert payload["max_tokens"] == 128
        assert payload["top_p"] == 0.9

    def test_response_parsing(self):
        adapter, _ = make_adapter()
        result = adapter.complete("hi")
        assert result.text == "ответ модели"
        assert result.provider == "gigachat"
        assert result.model == "GigaChat-Pro"
        assert result.finish_reason == "stop"
        assert result.usage.input_tokens == 8
        assert result.usage.total_tokens == 12

    def test_empty_choices_raises(self):
        transport = FakeTransport(chat_responder=lambda u, p, h: (200, {}))
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=transport,
        )
        with pytest.raises(ProviderRequestError, match="no choices"):
            adapter.complete("hi")


class TestErrorMapping:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors(self, status):
        transport = FakeTransport(chat_responder=lambda u, p, h: (status, {}))
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=transport,
        )
        with pytest.raises(ProviderAuthError):
            adapter.complete("hi")

    @pytest.mark.parametrize("status", [429, 500])
    def test_transient_errors(self, status):
        transport = FakeTransport(chat_responder=lambda u, p, h: (status, {}))
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=transport,
        )
        with pytest.raises(ProviderTransientError):
            adapter.complete("hi")

    def test_cert_error_hints_at_env_fixes(self):
        def boom(url, payload, headers, timeout):
            raise TransportError("cert", "CERTIFICATE_VERIFY_FAILED")

        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=boom,
        )
        with pytest.raises(ProviderRequestError) as excinfo:
            adapter.complete("hi")
        assert "GIGACHAT_CA_BUNDLE" in str(excinfo.value)
        assert "GIGACHAT_ALLOW_INSECURE" in str(excinfo.value)

    def test_network_error_is_transient(self):
        def boom(url, payload, headers, timeout):
            raise TransportError("network", "timeout")

        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="k", transport=boom,
        )
        with pytest.raises(ProviderTransientError):
            adapter.complete("hi")

    def test_oauth_error_says_oauth_stage(self):
        transport = FakeTransport(oauth_responder=lambda u, p, h: (401, {}))
        adapter = GigaChatAdapter(
            base_url=BASE, oauth_url=OAUTH, default_model="GigaChat-Pro",
            auth_key="bad", transport=transport
        )
        with pytest.raises(ProviderAuthError, match="oauth"):
            adapter.complete("hi")


class TestStream:
    def test_stream_is_one_final_chunk(self):
        adapter, _ = make_adapter()
        events = list(adapter.stream("hi"))
        assert len(events) == 1
        assert events[0].delta == "ответ модели"
        assert events[0].provider == "gigachat"


class TestBuild:
    def test_build_none_without_credentials(self):
        assert GigaChatAdapter.build({}, base_url=BASE, env={}) is None

    def test_build_reads_auth_key_env(self):
        adapter = GigaChatAdapter.build(
            {}, base_url=BASE, env={"GIGACHAT_AUTH_KEY": "k"}, transport=object()
        )
        assert adapter is not None

    def test_build_reads_access_token_env(self):
        adapter = GigaChatAdapter.build(
            {}, base_url=BASE, env={"GIGACHAT_ACCESS_TOKEN": "t"}, transport=object()
        )
        assert adapter is not None
        assert adapter._static_token is True

    def test_build_reads_scope_env(self):
        adapter = GigaChatAdapter.build(
            {},
            base_url=BASE,
            env={"GIGACHAT_AUTH_KEY": "k", "GIGACHAT_SCOPE": "GIGACHAT_API_B2B"},
            transport=object(),
        )
        assert adapter._scope == "GIGACHAT_API_B2B"

    def test_build_env_name_overrides(self):
        adapter = GigaChatAdapter.build(
            {"api_key_env": "MY_KEY"},
            base_url=BASE,
            env={"MY_KEY": "k"},
            transport=object(),
        )
        assert adapter is not None


class TestTlsPolicy:
    def test_allow_insecure_creates_unverified_context(self):
        context = GigaChatAdapter._ssl_context_for({"GIGACHAT_ALLOW_INSECURE": "1"})
        assert context is not None
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE

    def test_default_is_verification_on(self):
        assert GigaChatAdapter._ssl_context_for({}) is None

    def test_ca_bundle_uses_default_context(self, monkeypatch):
        captured = {}

        def fake_create(cafile=None):
            captured["cafile"] = cafile
            return "ctx-marker"

        monkeypatch.setattr(ssl, "create_default_context", fake_create)
        context = GigaChatAdapter._ssl_context_for({"GIGACHAT_CA_BUNDLE": "/ca.pem"})
        assert captured["cafile"] == "/ca.pem"
        assert context == "ctx-marker"
