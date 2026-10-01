"""Unit tests for the native YandexGPT adapter — all transport is faked."""

from __future__ import annotations

import ssl
import urllib.error

import pytest

from russian_llm_pack.providers._http import TransportError
from russian_llm_pack.providers.yandexgpt import YandexGPTAdapter
from russian_llm_pack.types import (
    ChatMessage,
    ProviderAuthError,
    ProviderRequestError,
    ProviderTransientError,
)

BASE = "https://llm.api.cloud.yandex.net/foundationModels/v1"


def yandex_body(text="готово", status="ALTERNATIVE_STATUS_FINAL"):
    return {
        "result": {
            "alternatives": [
                {"message": {"role": "assistant", "text": text}, "status": status}
            ],
            "usage": {
                "inputTextTokens": "12",
                "completionTokens": "7",
                "totalTokens": "19",
            },
            "modelVersion": "17.02.25",
        }
    }


class FakeTransport:
    """Records calls; replies via a responder(url, payload, headers)."""

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder or (lambda url, payload, headers: (200, yandex_body()))

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": dict(headers)})
        return self.responder(url, payload, headers)


def make_adapter(**kwargs):
    defaults = dict(
        base_url=BASE,
        api_key="test-api-key",
        folder_id="b1gabc123",
        default_model="yandexgpt",
        transport=FakeTransport(),
    )
    defaults.update(kwargs)
    return YandexGPTAdapter(**defaults)


class TestRequestBuilding:
    def test_url_and_model_uri(self):
        adapter = make_adapter()
        adapter.complete([ChatMessage.user("привет")])
        call = adapter._transport.calls[0]
        assert call["url"] == f"{BASE}/completion"
        assert call["payload"]["modelUri"] == "gpt://b1gabc123/yandexgpt"

    def test_messages_use_native_role_text_shape(self):
        adapter = make_adapter()
        adapter.complete(
            [ChatMessage.system("ты кодер"), ChatMessage.user("напиши функцию")]
        )
        messages = adapter._transport.calls[0]["payload"]["messages"]
        assert messages == [
            {"role": "system", "text": "ты кодер"},
            {"role": "user", "text": "напиши функцию"},
        ]

    def test_auth_and_folder_headers(self):
        adapter = make_adapter()
        adapter.complete("hi")
        headers = adapter._transport.calls[0]["headers"]
        assert headers["Authorization"] == "Api-Key test-api-key"
        assert headers["x-folder-id"] == "b1gabc123"
        assert headers["Content-Type"] == "application/json"

    def test_iam_token_uses_bearer(self):
        adapter = make_adapter(api_key=None, iam_token="iam-tok")
        adapter.complete("hi")
        headers = adapter._transport.calls[0]["headers"]
        assert headers["Authorization"] == "Bearer iam-tok"

    def test_api_key_wins_over_iam_token(self):
        adapter = make_adapter(iam_token="iam-tok")
        adapter.complete("hi")
        headers = adapter._transport.calls[0]["headers"]
        assert headers["Authorization"] == "Api-Key test-api-key"

    def test_completion_options_mapping(self):
        adapter = make_adapter()
        adapter.complete("hi", temperature=0.3, max_tokens=256)
        options = adapter._transport.calls[0]["payload"]["completionOptions"]
        assert options == {"temperature": 0.3, "maxTokens": 256}

    def test_explicit_model_uri_passthrough(self):
        adapter = make_adapter()
        adapter.complete("hi", model="ds://b1gabc123/fine-tuned-id")
        call = adapter._transport.calls[0]
        assert call["payload"]["modelUri"] == "ds://b1gabc123/fine-tuned-id"


class TestResponseParsing:
    def test_text_usage_latency(self):
        adapter = make_adapter()
        result = adapter.complete("hi")
        assert result.text == "готово"
        assert result.provider == "yandexgpt"
        assert result.model == "yandexgpt"
        assert result.usage.input_tokens == 12  # strings -> ints
        assert result.usage.output_tokens == 7
        assert result.usage.total_tokens == 19
        assert result.latency_ms >= 0

    @pytest.mark.parametrize(
        "api_status,expected",
        [
            ("ALTERNATIVE_STATUS_FINAL", "stop"),
            ("ALTERNATIVE_STATUS_TRUNCATED", "length"),
            ("ALTERNATIVE_STATUS_CONTENT_FILTER", "content_filter"),
            ("WEIRD_STATUS", "WEIRD_STATUS"),
        ],
    )
    def test_finish_reason_mapping(self, api_status, expected):
        adapter = make_adapter(transport=FakeTransport(
            lambda u, p, h: (200, yandex_body(status=api_status))
        ))
        assert adapter.complete("hi").finish_reason == expected

    def test_empty_alternatives_raises(self):
        adapter = make_adapter(transport=FakeTransport(lambda u, p, h: (200, {})))
        with pytest.raises(ProviderRequestError, match="no alternatives"):
            adapter.complete("hi")


class TestErrorMapping:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors(self, status):
        adapter = make_adapter(transport=FakeTransport(
            lambda u, p, h: (status, {"error": {"message": "нет доступа"}})
        ))
        with pytest.raises(ProviderAuthError, match="YANDEXGPT_API_KEY"):
            adapter.complete("hi")

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_errors(self, status):
        adapter = make_adapter(transport=FakeTransport(
            lambda u, p, h: (status, {"error": {"message": "превышен лимит"}})
        ))
        with pytest.raises(ProviderTransientError):
            adapter.complete("hi")

    def test_request_error_for_other_4xx(self):
        adapter = make_adapter(transport=FakeTransport(lambda u, p, h: (400, {})))
        with pytest.raises(ProviderRequestError, match="HTTP 400"):
            adapter.complete("hi")

    def test_transport_network_error_is_transient(self):
        def boom(url, payload, headers, timeout):
            raise TransportError("network", "URLError: DNS failure")

        adapter = make_adapter(transport=boom)
        with pytest.raises(ProviderTransientError, match="DNS"):
            adapter.complete("hi")

    def test_no_credentials_at_call_time(self):
        adapter = make_adapter(api_key=None, iam_token=None)
        with pytest.raises(ProviderAuthError):
            adapter.complete("hi")

    def test_missing_folder_raises_clear_error(self):
        adapter = make_adapter(folder_id=None)
        with pytest.raises(ProviderRequestError, match="YANDEXGPT_FOLDER_ID"):
            adapter.complete("hi")


class TestStream:
    def test_stream_is_one_final_chunk(self):
        adapter = make_adapter()
        events = list(adapter.stream("hi"))
        assert len(events) == 1
        assert events[0].delta == "готово"
        assert events[0].finish_reason == "stop"
        assert events[0].provider == "yandexgpt"


class TestBuild:
    def test_build_returns_none_without_credentials(self):
        assert YandexGPTAdapter.build({}, base_url=BASE, env={}) is None

    def test_build_returns_none_without_folder(self):
        adapter = YandexGPTAdapter.build(
            {}, base_url=BASE, env={"YANDEXGPT_API_KEY": "k"}, transport=object()
        )
        assert adapter is None

    def test_build_reads_env(self):
        adapter = YandexGPTAdapter.build(
            {},
            base_url=BASE,
            default_model="yandexgpt",
            env={"YANDEXGPT_API_KEY": "k", "YANDEXGPT_FOLDER_ID": "f1"},
            transport=object(),
        )
        assert adapter is not None
        assert adapter.default_model == "yandexgpt"
        assert adapter.base_url == BASE

    def test_build_iam_env(self):
        adapter = YandexGPTAdapter.build(
            {},
            base_url=BASE,
            env={"YANDEXGPT_IAM_TOKEN": "t", "YANDEXGPT_FOLDER_ID": "f1"},
            transport=object(),
        )
        assert adapter is not None

    def test_build_respects_env_name_overrides(self):
        adapter = YandexGPTAdapter.build(
            {"api_key_env": "MY_KEY", "folder_id_env": "MY_FOLDER"},
            base_url=BASE,
            env={"MY_KEY": "k", "MY_FOLDER": "f"},
            transport=object(),
        )
        assert adapter is not None

    def test_build_base_url_override(self):
        adapter = YandexGPTAdapter.build(
            {"base_url": "https://example.com/v1"},
            base_url=BASE,
            env={"YANDEXGPT_API_KEY": "k", "YANDEXGPT_FOLDER_ID": "f"},
            transport=object(),
        )
        assert adapter.base_url == "https://example.com/v1"

    def test_explicit_api_key_beats_env(self):
        adapter = YandexGPTAdapter.build(
            {},
            base_url=BASE,
            api_key="explicit",
            folder_id="f1",
            env={"YANDEXGPT_API_KEY": "from-env"},
            transport=object(),
        )
        assert adapter._api_key == "explicit"


class TestRealTransportErrorPath:
    def test_urlerror_cert_reason_is_classified_as_cert(self):
        from russian_llm_pack.providers._http import _classify_url_error

        inner = ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
        )
        outer = urllib.error.URLError(inner)
        error = _classify_url_error(outer)
        assert error.reason == "cert"

    def test_urlerror_plain_reason_is_network(self):
        from russian_llm_pack.providers._http import _classify_url_error

        outer = urllib.error.URLError(OSError("refused"))
        error = _classify_url_error(outer)
        assert error.reason == "network"
