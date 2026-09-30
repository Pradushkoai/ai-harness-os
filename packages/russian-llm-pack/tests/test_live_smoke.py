"""LIVE smoke tests — hit real provider APIs, cost real (tiny) money.

Run explicitly (unit CI never runs them):
    DEEPSEEK_API_KEY=sk-... pytest -m live -v

Each test sends one minimal request (max_tokens <= 32).
"""

from __future__ import annotations

import os

import pytest

from russian_llm_pack.core.config import RouterConfig
from russian_llm_pack.core.router import Router
from russian_llm_pack.providers.registry import build_provider
from russian_llm_pack.types import ChatMessage

pytestmark = pytest.mark.live


def test_deepseek_adapter_smoke():
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        pytest.skip("DEEPSEEK_API_KEY is not set")
    adapter = build_provider("deepseek")
    assert adapter is not None, "provider did not build despite the key"

    result = adapter.complete(
        [ChatMessage.user("Ответь ровно одним словом: работает?")],
        max_tokens=32,
        temperature=0.0,
    )
    assert result.text.strip(), f"empty completion: {result!r}"
    assert result.usage.total_tokens > 0
    assert result.latency_ms > 0
    print(f"\n[smoke] deepseek -> {result.text!r} "
          f"({result.usage.total_tokens} tok, {result.latency_ms:.0f} ms)")


def test_router_smoke_default_task():
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        pytest.skip("DEEPSEEK_API_KEY is not set")

    router = Router.from_config(RouterConfig.builtin())
    result = router.complete("coding", "Ответь ровно одним словом: работает?", max_tokens=32)
    assert result.text.strip()
    print(f"\n[smoke] router(coding) -> {result.provider}/{result.model}: {result.text!r}")
