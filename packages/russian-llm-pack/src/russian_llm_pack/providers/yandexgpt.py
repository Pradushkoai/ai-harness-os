"""Native YandexGPT adapter — Yandex Foundation Models REST API.

Why native: the completion endpoint is NOT OpenAI-compatible — `modelUri`
instead of `model`, `{role, text}` messages instead of `{role, content}`,
`x-folder-id` headers — so the generic engine does not apply.

Protocol (POST {base_url}/completion):
    headers:   Authorization: Api-Key <key> | Bearer <iam-token>, x-folder-id
    request:   {modelUri, completionOptions: {temperature, maxTokens},
                messages: [{role, text}]}
    response:  {result: {alternatives: [{message: {role, text}, status}],
                usage: {inputTextTokens, completionTokens, totalTokens}}}

Credentials (env, names overridable via config):
    YANDEXGPT_API_KEY    — service-account API key; static; preferred
    YANDEXGPT_IAM_TOKEN  — IAM bearer token; ~12h TTL; NOT auto-refreshed
                           (refreshing needs an OAuth app — out of scope)
    YANDEXGPT_FOLDER_ID  — required for modelUri: gpt://<folder_id>/<model>

A model containing "://" (an explicit `gpt://...` / `ds://...` fine-tuned
URI) is passed through as-is, so power users can pin exact versions.

Streaming: the sync API is request/response; `stream()` is emulated as one
final chunk (the harness only consumes `complete()` today).
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from ..types import (
    CompletionResult,
    ProviderAuthError,
    ProviderError,
    ProviderRequestError,
    ProviderTransientError,
    StreamEvent,
    UsageInfo,
    coerce_messages,
)
from ._http import JsonTransport, TransportError

__all__ = ["YandexGPTAdapter"]


def _to_int(value) -> int:
    """Tolerant int(): Yandex sends token counts as strings."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class YandexGPTAdapter:
    """Adapter for the Yandex Foundation Models completion API."""

    ENV_API_KEY = "YANDEXGPT_API_KEY"
    ENV_IAM_TOKEN = "YANDEXGPT_IAM_TOKEN"
    ENV_FOLDER_ID = "YANDEXGPT_FOLDER_ID"

    def __init__(
        self,
        *,
        name: str = "yandexgpt",
        base_url: str,
        api_key: str | None = None,
        iam_token: str | None = None,
        folder_id: str | None = None,
        default_model: str | None = None,
        timeout: float = 60.0,
        transport: Any = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._api_key = api_key or None
        self._iam_token = iam_token or None
        self._folder_id = folder_id or None
        self._timeout = timeout
        self._transport = transport if transport is not None else JsonTransport()

    # -- construction ---------------------------------------------------------

    @classmethod
    def build(
        cls,
        overrides: Mapping | None = None,
        *,
        base_url: str,
        default_model: str | None = None,
        timeout: float = 60.0,
        transport: Any = None,
        api_key: str | None = None,
        iam_token: str | None = None,
        folder_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> YandexGPTAdapter | None:
        """Build from env (+ config overrides); None when unusable.

        Returns None (the Router then skips the provider) when neither an
        API key nor an IAM token is present, or when the folder id is
        missing — the folder is as mandatory as the key on this API.
        """

        overrides = dict(overrides or {})
        source = os.environ if env is None else env

        resolved_base = str(overrides.get("base_url") or base_url).rstrip("/")
        resolved_model = overrides.get("default_model") or default_model
        key_env = str(overrides.get("api_key_env") or cls.ENV_API_KEY)
        token_env = str(overrides.get("iam_token_env") or cls.ENV_IAM_TOKEN)
        folder_env = str(overrides.get("folder_id_env") or cls.ENV_FOLDER_ID)

        resolved_key = api_key or source.get(key_env) or None
        resolved_token = iam_token or source.get(token_env) or None
        resolved_folder = folder_id or source.get(folder_env) or None

        if not (resolved_key or resolved_token) or not resolved_folder:
            return None

        return cls(
            base_url=resolved_base,
            api_key=resolved_key,
            iam_token=resolved_token,
            folder_id=resolved_folder,
            default_model=str(resolved_model) if resolved_model else None,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def credential_envs(cls) -> tuple[str, ...]:
        """Env var names that count as 'has credentials' for `rlp check`."""
        return (cls.ENV_API_KEY, cls.ENV_IAM_TOKEN)

    # -- LLMPort implementation ----------------------------------------------

    def complete(
        self,
        messages,
        *,
        model: str | None = None,
        **kwargs,
    ) -> CompletionResult:
        msgs = coerce_messages(messages)
        model = model or self.default_model
        if not model:
            raise ProviderRequestError(
                f"[{self.name}] no model given and adapter has no default_model"
            )

        headers = self._auth_headers()
        payload = {
            "modelUri": self._model_uri(model),
            "completionOptions": self._completion_options(kwargs),
            "messages": [{"role": m.role, "text": m.content} for m in msgs],
        }

        started = time.monotonic()
        status, body = self._post(f"{self.base_url}/completion", payload, headers)
        latency_ms = (time.monotonic() - started) * 1000.0

        result = body.get("result") or {}
        alternatives = result.get("alternatives") or []
        if not alternatives:
            raise ProviderRequestError(
                f"[{self.name}/{model}] response has no alternatives "
                f"(status {status}): {str(body)[:200]}"
            )
        alternative = alternatives[0]
        message = alternative.get("message") or {}
        return CompletionResult(
            text=str(message.get("text") or ""),
            provider=self.name,
            model=model,
            usage=self._usage(result.get("usage")),
            latency_ms=latency_ms,
            finish_reason=self._finish_reason(alternative),
            raw=body,
        )

    def stream(self, messages, *, model: str | None = None, **kwargs):
        """Emulated streaming: one final chunk (sync API is request/response)."""
        result = self.complete(messages, model=model, **kwargs)
        yield StreamEvent(
            delta=result.text,
            provider=result.provider,
            model=result.model,
            finish_reason=result.finish_reason,
        )

    # -- protocol mapping ------------------------------------------------------

    def _model_uri(self, model: str) -> str:
        if "://" in model:
            return model  # explicit gpt://…/yandexgpt@rc or ds://… passthrough
        if not self._folder_id:
            raise ProviderRequestError(
                f"[{self.name}] folder id missing — set {self.ENV_FOLDER_ID}"
            )
        return f"gpt://{self._folder_id}/{model}"

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-folder-id": self._folder_id or "",
        }
        if self._api_key:  # API key wins when both are configured
            headers["Authorization"] = f"Api-Key {self._api_key}"
        elif self._iam_token:
            headers["Authorization"] = f"Bearer {self._iam_token}"
        else:
            raise ProviderAuthError(
                f"[{self.name}] no credentials — set {self.ENV_API_KEY} "
                f"or {self.ENV_IAM_TOKEN}"
            )
        return headers

    @staticmethod
    def _completion_options(kwargs: dict) -> dict:
        options: dict[str, Any] = {}
        if kwargs.get("temperature") is not None:
            options["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            options["maxTokens"] = int(kwargs["max_tokens"])
        return options

    @staticmethod
    def _usage(raw) -> UsageInfo:
        raw = raw or {}
        return UsageInfo(
            input_tokens=_to_int(raw.get("inputTextTokens")),
            output_tokens=_to_int(raw.get("completionTokens")),
            total_tokens=_to_int(raw.get("totalTokens")),
            raw=dict(raw),
        )

    @staticmethod
    def _finish_reason(alternative: dict) -> str | None:
        status = str(alternative.get("status") or "")
        if status == "ALTERNATIVE_STATUS_FINAL":
            return "stop"
        if "TRUNCATED" in status:
            return "length"
        if "CONTENT_FILTER" in status:
            return "content_filter"
        return status or None

    # -- transport ----------------------------------------------------------

    def _post(self, url: str, payload: dict, headers: dict) -> tuple[int, dict]:
        try:
            status, body = self._transport(url, payload, headers, self._timeout)
        except TransportError as exc:
            raise ProviderTransientError(
                f"[{self.name}] transport failure ({exc.reason}): {exc}"
            ) from exc
        if status >= 400:
            raise self._http_error(status, body)
        return status, body

    def _http_error(self, status: int, body: dict) -> ProviderError:
        error = body.get("error") or {}
        detail = error.get("message") or str(body)[:200]
        where = f"[{self.name}] HTTP {status}"
        if status in (401, 403):
            return ProviderAuthError(
                f"{where}: {detail} (check {self.ENV_API_KEY} / "
                f"{self.ENV_IAM_TOKEN} / {self.ENV_FOLDER_ID})"
            )
        if status == 429 or status >= 500:
            return ProviderTransientError(f"{where}: {detail}")
        return ProviderRequestError(f"{where}: {detail}")

    def __repr__(self) -> str:  # pragma: no cover — debugging nicety
        auth = "api-key" if self._api_key else ("iam" if self._iam_token else "none")
        return (
            f"YandexGPTAdapter(name={self.name!r}, base_url={self.base_url!r}, "
            f"auth={auth}, default_model={self.default_model!r})"
        )
