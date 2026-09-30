"""LIVE tests — the real loop: real LLM (provider key required) + real verifier.

Run explicitly (needs both a provider key and the jar):
    DEEPSEEK_API_KEY=sk-... BSL_LS_JAR=/path/to/bsl-language-server.jar \
        pytest -m live -v

These are the only tests that spend tokens (~a few thousand per run).
"""

from __future__ import annotations

import os
import shutil

import pytest
from bsl_verify import BslVerifier
from russian_llm_pack import Router, load_config

from harness_loop import BslAgentLoop, LoopConfig, RouterPort

pytestmark = pytest.mark.live


def _live_available() -> tuple[bool, str]:
    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ZAI_API_KEY")):
        return False, "no provider key (set e.g. DEEPSEEK_API_KEY)"
    if not (
        os.environ.get("BSL_LS_JAR")
        or (
            shutil.which("java")
            and (
                os.path.exists("bsl-language-server.jar")
                or os.path.exists(
                    os.path.expanduser("~/.bsl-language-server/bsl-language-server.jar")
                )
            )
        )
    ):
        return False, "bsl-language-server.jar not available (set BSL_LS_JAR)"
    return True, ""


@pytest.fixture(scope="module")
def live_setup():
    ok, reason = _live_available()
    if not ok:
        pytest.skip(reason)
    router = Router.from_config(load_config()[0])
    llm_port = RouterPort(router, task="coding")
    verifier = BslVerifier(timeout_s=300.0)
    return llm_port, verifier


class TestLiveLoop:
    def test_writes_a_passing_module(self, live_setup):
        llm_port, verifier = live_setup
        loop = BslAgentLoop(
            llm=llm_port, verifier=verifier, config=LoopConfig(max_iterations=3)
        )

        result = loop.run(
            "Напиши функцию СуммаДвухЧисел(А, Б), которая возвращает сумму двух чисел. "
            "Модуль должен содержать только эту функцию."
        )

        assert len(result.iterations) >= 1
        assert result.code.strip(), "final code must not be empty"
        assert "Функция" in result.code
        assert result.passed is True, "\n".join(result.summary_lines())
