"""Native GigaChat adapter — Sberbank GigaChat API with OAuth.

Why native: the OAuth dance (Basic credentials -> short-lived access token)
and the Russian-CA TLS chain make the plain OpenAI engine a poor fit — the
token refresh belongs inside the adapter, not in every caller.

Flow:
    1. POST {oauth_url}   Authorization: Basic <GIGACHAT_AUTH_KEY>,
                          RqUID: <uuid4>, Content-Type: x-www-form-urlencoded,
                          body: scope=GIGACHAT_API_PERS
       -> {"access_token": …, "expires_at": <unix-ms>}
    2. POST {base_url}/chat/completions   Authorization: Bearer <token>
       OpenAI-shaped request/response (messages are {role, content} strings).

Credentials (env, names overridable via config):
    GIGACHAT_AUTH_KEY      — base64(client_id:client_secret) from the Sber
                             developer portal; enables the OAuth flow
    GIGACHAT_ACCESS_TOKEN  — a ready access token; OAuth is skipped entirely
                             (and never refreshed — useful for quick tests)
    GIGACHAT_SCOPE         — OAuth scope (default GIGACHAT_API_PERS)

TLS: both endpoints are signed by the Russian Trusted CA, which is absent
from default Linux/CA stores. Verification stays ON by default; when the
handshake fails with a certificate error the adapter raises a
ProviderRequestError naming the two fixes — install the CA bundle and point
GIGACHAT_CA_BUNDLE at it, or opt out with GIGACHAT_ALLOW_INSECURE=1 (dev
only). The token cache is per-instance and single-threaded, matching the
CLI/loop usage; margin REFRESH_S is subtracted from expires_at.

Streaming: SSE exists upstream but the sync API is what the harness uses;
`stream()` is emulated as one final chunk.
"""

from __future__ import annotations

import os
import ssl
import time
import uuid
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

__all__ = ["GigaChatAdapter"]


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class GigaChatAdapter:
    """Adapter for the GigaChat chat API with built-in OAuth token refresh."""

    ENV_AUTH_KEY = "GIGACHAT_AUTH_KEY"
    ENV_ACCESS_TOKEN = "GIGACHAT_ACCESS_TOKEN"
    ENV_SCOPE = "GIGACHAT_SCOPE"
    ENV_ALLOW_INSECURE = "GIGACHAT_ALLOW_INSECURE"
    ENV_CA_BUNDLE = "GIGACHAT_CA_BUNDLE"

    DEFAULT_SCOPE = "GIGACHAT_API_PERS"
    REFRESH_MARGIN_MS = 60_000  # refresh a minute before real expiry
    _DEFAULT_TTL_MS = 30 * 60_000  # assume 30 min when expires_at is absent

    def __init__(
        self,
        *,
        name: str = "gigachat",
        base_url: str,
        oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        auth_key: str | None = None,
        access_token: str | None = None,
        scope: str = DEFAULT_SCOPE,
        default_model: str | None = None,
        timeout: float = 60.0,
        transport: Any = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._oauth_url = oauth_url
        self.default_model = default_model
        self._auth_key = auth_key or None
        self._scope = scope or self.DEFAULT_SCOPE
        self._timeout = timeout
        self._transport = transport if transport is not None else JsonTransport()

        self._token: str | None = access_token or None
        self._static_token = bool(access_token)  # user-supplied: never refreshed
        self._expires_at_ms = 0

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
        auth_key: str | None = None,
        access_token: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> GigaChatAdapter | None:
        """Build from env (+ config overrides); None when unusable.

        Returns None (the Router then skips the provider) when neither an
        auth key nor an access token is present. TLS policy env vars are
        honoured only when no transport is injected (tests inject fakes).
        """

        overrides = dict(overrides or {})
        source = os.environ if env is None else env

        resolved_base = str(overrides.get("base_url") or base_url).rstrip("/")
        resolved_model = overrides.get("default_model") or default_model
        key_env = str(overrides.get("api_key_env") or cls.ENV_AUTH_KEY)
        token_env = str(overrides.get("access_token_env") or cls.ENV_ACCESS_TOKEN)
        scope_env = str(overrides.get("scope_env") or cls.ENV_SCOPE)
        oauth_url = str(overrides.get("oauth_url") or cls._default_oauth_url())

        resolved_key = auth_key or source.get(key_env) or None
        resolved_token = access_token or source.get(token_env) or None
        resolved_scope = source.get(scope_env) or cls.DEFAULT_SCOPE

        if not (resolved_key or resolved_token):
            return None

        resolved_transport = transport
        if resolved_transport is None:
            resolved_transport = JsonTransport(
                ssl_context=cls._ssl_context_for(source)
            )

        return cls(
            base_url=resolved_base,
            oauth_url=oauth_url,
            auth_key=resolved_key,
            access_token=resolved_token,
            scope=str(resolved_scope),
            default_model=str(resolved_model) if resolved_model else None,
            timeout=timeout,
            transport=resolved_transport,
        )

    @classmethod
    def credential_envs(cls) -> tuple[str, ...]:
        """Env var names that count as 'has credentials' for `rlp check`."""
        return (cls.ENV_AUTH_KEY, cls.ENV_ACCESS_TOKEN)

    @classmethod
    def _default_oauth_url(cls) -> str:
        return "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    @classmethod
    def _ssl_context_for(cls, env: Mapping[str, str]) -> ssl.SSLContext | None:
        """TLS policy from env: CA bundle > allow-insecure > default verify."""

        allow = str(env.get(cls.ENV_ALLOW_INSECURE, "")).strip().lower()
        if allow in ("1", "true", "yes"):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        ca_bundle = str(env.get(cls.ENV_CA_BUNDLE, "")).strip()
        if ca_bundle:
            return ssl.create_default_context(cafile=ca_bundle)
        return None  # standard verification

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

        token = self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content} for m in msgs
            ],
        }
        for key in ("temperature", "max_tokens", "top_p"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]

        started = time.monotonic()
        status, body = self._post(f"{self.base_url}/chat/completions", payload, headers)
        latency_ms = (time.monotonic() - started) * 1000.0

        choices = body.get("choices") or []
        if not choices:
            raise ProviderRequestError(
                f"[{self.name}/{model}] response has no choices "
                f"(status {status}): {str(body)[:200]}"
            )
        choice = choices[0]
        message = choice.get("message") or {}
        usage_raw = body.get("usage") or {}
        return CompletionResult(
            text=str(message.get("content") or ""),
            provider=self.name,
            model=model,
            usage=UsageInfo(
                input_tokens=_to_int(usage_raw.get("prompt_tokens")),
                output_tokens=_to_int(usage_raw.get("completion_tokens")),
                total_tokens=_to_int(usage_raw.get("total_tokens")),
                raw=dict(usage_raw),
            ),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason") or None,
            raw=body,
        )

    def stream(self, messages, *, model: str | None = None, **kwargs):
        """Emulated streaming: one final chunk (SSE parsing is future work)."""
        result = self.complete(messages, model=model, **kwargs)
        yield StreamEvent(
            delta=result.text,
            provider=result.provider,
            model=result.model,
            finish_reason=result.finish_reason,
        )

    # -- OAuth ----------------------------------------------------------------

    def _access_token(self) -> str:
        """Return a live access token, fetching/refreshing via OAuth as needed."""

        if self._static_token:
            token = self._token
            if not token:
                raise ProviderAuthError(
                    f"[{self.name}] static access token is empty — "
                    f"set {self.ENV_ACCESS_TOKEN}"
                )
            return token

        now_ms = int(time.time() * 1000.0)
        if self._token and self._expires_at_ms > now_ms + self.REFRESH_MARGIN_MS:
            return self._token

        if not self._auth_key:
            raise ProviderAuthError(
                f"[{self.name}] no auth key — set {self.ENV_AUTH_KEY}"
            )

        headers = {
            "Authorization": f"Basic {self._auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = f"scope={self._scope}"

        status, body = self._post(self._oauth_url, payload, headers, oauth=True)

        token = str(body.get("access_token") or "")
        if not token:
            raise ProviderAuthError(
                f"[{self.name}] OAuth response has no access_token "
                f"(status {status}): {str(body)[:200]}"
            )
        expires_at = _to_int(body.get("expires_at"))
        if expires_at <= 0:
            expires_at = now_ms + self._DEFAULT_TTL_MS
        self._token = token
        self._expires_at_ms = expires_at
        return token

    # -- transport ----------------------------------------------------------

    def _post(
        self, url: str, payload, headers: dict, *, oauth: bool = False
    ) -> tuple[int, dict]:
        try:
            status, body = self._transport(url, payload, headers, self._timeout)
        except TransportError as exc:
            raise self._map_transport_error(exc) from exc
        if status >= 400:
            raise self._http_error(status, body, oauth=oauth)
        return status, body

    def _map_transport_error(self, exc: TransportError) -> ProviderError:
        if exc.reason == "cert":
            return ProviderRequestError(
                f"[{self.name}] TLS certificate verification failed: {exc}. "
                "GigaChat endpoints use the Russian Trusted CA. Fix: install "
                "the CA bundle and set GIGACHAT_CA_BUNDLE, or set "
                "GIGACHAT_ALLOW_INSECURE=1 to skip verification (dev only)."
            )
        return ProviderTransientError(
            f"[{self.name}] transport failure ({exc.reason}): {exc}"
        )

    def _http_error(self, status: int, body: dict, *, oauth: bool = False) -> ProviderError:
        detail = body.get("message") or body.get("detail") or str(body)[:200]
        stage = "oauth" if oauth else "chat"
        where = f"[{self.name}/{stage}] HTTP {status}"
        if status in (401, 403):
            return ProviderAuthError(
                f"{where}: {detail} (check {self.ENV_AUTH_KEY} / scope)"
            )
        if status == 429 or status >= 500:
            return ProviderTransientError(f"{where}: {detail}")
        return ProviderRequestError(f"{where}: {detail}")

    def __repr__(self) -> str:  # pragma: no cover — debugging nicety
        mode = "static-token" if self._static_token else "oauth"
        return (
            f"GigaChatAdapter(name={self.name!r}, base_url={self.base_url!r}, "
            f"auth={mode}, default_model={self.default_model!r})"
        )
