"""Provider presets and factory.

A preset is pure metadata: base_url, env var name, known models. No secrets.
`build_provider()` instantiates an adapter from a preset + config overrides,
returning None when the provider has no API key configured — the Router then
simply skips it, which is what makes partial setups (e.g. only DeepSeek)
work out of the box.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .base import OpenAICompatibleAdapter


@dataclass(frozen=True)
class ProviderPreset:
    """Static description of a provider. Keys are never stored here."""

    name: str
    base_url: str
    api_key_env: str
    models: tuple = ()
    default_model: Optional[str] = None
    experimental: bool = False
    native: bool = False  # native = not OpenAI-compatible (needs own adapter)
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
        api_key_env="GIGACHAT_ACCESS_TOKEN",
        models=("GigaChat-Max", "GigaChat-Pro", "GigaChat-Lite"),
        default_model="GigaChat-Pro",
        experimental=True,
        notes=(
            "OAuth access token must be obtained out-of-band "
            "(client-credentials flow, scope like GIGACHAT_API_PERS). "
            "The OAuth helper itself is planned for v0.2."
        ),
    ),
    "yandexgpt": ProviderPreset(
        name="yandexgpt",
        base_url="https://llm.api.cloud.yandex.net/foundationModels/v1",
        api_key_env="YAIAM_TOKEN",
        models=("yandexgpt", "yandexgpt-pro", "yandexgpt-lite"),
        default_model="yandexgpt",
        native=True,
        notes=(
            "Yandex Foundation Models API is NOT OpenAI-compatible "
            "(modelUri + x-folder-id headers). Native adapter is planned "
            "for v0.2; the Router currently skips this provider."
        ),
    ),
}


def preset_info(name: str, overrides: Optional[Mapping] = None) -> dict[str, Any]:
    """Describe a provider for `rlp check`: preset + overrides + key status."""

    if name not in PRESETS:
        return {"name": name, "unknown": True, "notes": "no preset — custom provider?"}
    preset = PRESETS[name]
    overrides = dict(overrides or {})
    base_url = overrides.get("base_url") or preset.base_url
    api_key_env = overrides.get("api_key_env") or preset.api_key_env
    default_model = overrides.get("default_model") or preset.default_model
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
    overrides: Optional[Mapping] = None,
    *,
    api_key: Optional[str] = None,
    client: Any = None,
    timeout: float = 60.0,
) -> Optional[OpenAICompatibleAdapter]:
    """Instantiate an adapter from a preset (+ config overrides).

    Returns None (instead of raising) when the provider cannot be used:
      - unknown provider name
      - native (non-OpenAI-compatible) provider without an adapter yet
      - no API key found in the environment
    The Router treats None as "skip this provider".

    `api_key` argument exists for tests and programmatic use; in production
    the key always comes from the environment variable.
    """

    if name not in PRESETS:
        return None
    preset = PRESETS[name]
    if preset.native:
        return None

    overrides = dict(overrides or {})
    base_url = overrides.get("base_url") or preset.base_url
    api_key_env = overrides.get("api_key_env") or preset.api_key_env
    default_model = overrides.get("default_model") or preset.default_model

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
