"""Provider presets and factory.

A preset is pure metadata: base_url, env var name, known models. No secrets.
`build_provider()` instantiates an adapter from a preset + config overrides,
returning None when the provider has no API key configured — the Router then
simply skips it, which is what makes partial setups (e.g. only DeepSeek)
work out of the box.

Two adapter families:
    - generic: `OpenAICompatibleAdapter` (DeepSeek, Z.ai) — one engine,
      injectable `client` for tests;
    - native: dedicated adapters (YandexGPT, GigaChat) for APIs that are
      not OpenAI-compatible or need OAuth/TLS handling — injectable
      `transport` for tests. See NATIVE_ADAPTERS below.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .base import OpenAICompatibleAdapter
from .gigachat import GigaChatAdapter
from .yandexgpt import YandexGPTAdapter

# Providers with dedicated (non-generic) adapters, keyed by preset name.
# Every `native=True` preset MUST appear here — otherwise it is never built.
NATIVE_ADAPTERS: dict[str, type] = {
    "yandexgpt": YandexGPTAdapter,
    "gigachat": GigaChatAdapter,
}


@dataclass(frozen=True)
class ProviderPreset:
    """Static description of a provider. Keys are never stored here."""

    name: str
    base_url: str
    api_key_env: str
    models: tuple = ()
    default_model: str | None = None
    experimental: bool = False
    native: bool = False  # native = dedicated adapter, not the generic engine
    notes: str = ""


PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        models=("deepseek-chat", "deepseek-reasoner"),
        default_model="deepseek-chat",
        notes="OpenAI-compatible; deepseek-reasoner = chain-of-thought model",
    ),
    "zai": ProviderPreset(
        name="zai",
        # International endpoint; mainland alternative: https://open.bigmodel.cn/api/paas/v4
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        models=("glm-4.6", "glm-4.5-air", "glm-4.5-flash", "glm-5.2"),
        default_model="glm-4.6",
        notes="OpenAI-compatible; glm-5.2 targets long-context / long-horizon work",
    ),
    "gigachat": ProviderPreset(
        name="gigachat",
        base_url="https://gigachat.devices.sberbank.ru/api/v1",
        api_key_env="GIGACHAT_AUTH_KEY",
        models=("GigaChat-Max", "GigaChat-Pro", "GigaChat-Lite"),
        default_model="GigaChat-Pro",
        native=True,
        experimental=True,
        notes=(
            "Native adapter (v0.2): OAuth Basic GIGACHAT_AUTH_KEY -> access "
            "token (auto-refresh, ~30 min); GIGACHAT_ACCESS_TOKEN bypasses "
            "OAuth. TLS: Russian CA — set GIGACHAT_CA_BUNDLE or "
            "GIGACHAT_ALLOW_INSECURE=1 if the handshake fails."
        ),
    ),
    "yandexgpt": ProviderPreset(
        name="yandexgpt",
        base_url="https://llm.api.cloud.yandex.net/foundationModels/v1",
        api_key_env="YANDEXGPT_API_KEY",
        models=("yandexgpt", "yandexgpt-pro", "yandexgpt-lite"),
        default_model="yandexgpt",
        native=True,
        experimental=True,
        notes=(
            "Native adapter (v0.2): Api-Key YANDEXGPT_API_KEY (preferred) or "
            "Bearer YANDEXGPT_IAM_TOKEN (12h TTL, no auto-refresh); requires "
            "YANDEXGPT_FOLDER_ID — modelUri = gpt://<folder>/<model>."
        ),
    ),
}


def preset_info(name: str, overrides: Mapping | None = None) -> dict[str, Any]:
    """Describe a provider for `rlp check`: preset + overrides + key status."""

    if name not in PRESETS:
        return {"name": name, "unknown": True, "notes": "no preset — custom provider?"}
    preset = PRESETS[name]
    overrides = dict(overrides or {})
    base_url = overrides.get("base_url") or preset.base_url
    api_key_env = overrides.get("api_key_env") or preset.api_key_env
    default_model = overrides.get("default_model") or preset.default_model

    if name in NATIVE_ADAPTERS and not overrides.get("api_key_env"):
        # Any of the adapter's credential envs counts as "has key" (e.g. an
        # IAM token for Yandex, a ready access token for GigaChat).
        has_key = any(os.environ.get(e) for e in NATIVE_ADAPTERS[name].credential_envs())
    else:
        has_key = bool(os.environ.get(api_key_env))
    return {
        "name": name,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "has_key": has_key,
        "models": list(preset.models),
        "default_model": default_model,
        "experimental": preset.experimental,
        "native": preset.native,
        "notes": preset.notes,
    }


def build_provider(
    name: str,
    overrides: Mapping | None = None,
    *,
    api_key: str | None = None,
    client: Any = None,
    timeout: float = 60.0,
    transport: Any = None,
) -> Any | None:
    """Instantiate an adapter from a preset (+ config overrides).

    Returns None (instead of raising) when the provider cannot be used:
      - unknown provider name
      - no API key found in the environment (native adapters also require
        their extra env vars, e.g. YANDEXGPT_FOLDER_ID)
    The Router treats None as "skip this provider".

    `api_key` argument exists for tests and programmatic use; in production
    the key always comes from the environment variable. `client` injects a
    fake client into the OpenAI-compatible engine, `transport` a fake
    transport into native adapters (tests only).
    """

    if name not in PRESETS:
        return None
    preset = PRESETS[name]
    overrides = dict(overrides or {})
    base_url = overrides.get("base_url") or preset.base_url
    default_model = overrides.get("default_model") or preset.default_model

    if name in NATIVE_ADAPTERS:
        build_kwargs: dict[str, Any] = {}
        if transport is not None:
            build_kwargs["transport"] = transport
        if api_key:
            # The generic `api_key` maps to each adapter's primary credential:
            # YandexGPT -> Api-Key, GigaChat -> OAuth Basic auth key.
            if name == "gigachat":
                build_kwargs["auth_key"] = api_key
            else:
                build_kwargs["api_key"] = api_key
        return NATIVE_ADAPTERS[name].build(
            overrides,
            base_url=str(base_url),
            default_model=default_model,
            timeout=timeout,
            **build_kwargs,
        )

    api_key_env = overrides.get("api_key_env") or preset.api_key_env
    if api_key is None:
        api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return None

    return OpenAICompatibleAdapter(
        name=name,
        base_url=str(base_url),
        api_key=api_key,
        default_model=default_model,
        timeout=timeout,
        client=client,
    )
