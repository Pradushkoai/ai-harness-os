"""INTEGRATION tests — the real bsl-language-server behind the loop.

The LLM side is scripted (FakeLLMPort) — deterministic and free — while
the verifier side is the REAL BslVerifier with the real jar. This is the
end-to-end proof of the backpressure concept: a model that returns
broken code on iteration 1 gets REAL ParseError diagnostics and fixes
the module on iteration 2.

Run explicitly:
    BSL_LS_JAR=/path/to/bsl-language-server.jar pytest -m integration -v
"""

from __future__ import annotations

import os
import shutil

import pytest
from bsl_verify import BslVerifier

from harness_loop import BslAgentLoop, LoopConfig

from conftest import MODULE_BROKEN, MODULE_OK, FakeLLMPort, fenced

pytestmark = pytest.mark.integration


def jar_available() -> bool:
    if os.environ.get("BSL_LS_JAR"):
        return True
    return shutil.which("java") is not None and (
        os.path.exists("bsl-language-server.jar")
        or os.path.exists(os.path.expanduser("~/.bsl-language-server/bsl-language-server.jar"))
    )


@pytest.fixture(scope="module")
def real_verifier() -> BslVerifier:
    if not jar_available():
        pytest.skip("bsl-language-server.jar not available (set BSL_LS_JAR)")
    return BslVerifier(timeout_s=300.0)


class TestRealBackpressureLoop:
    def test_broken_then_fixed_passes_on_second_iteration(self, real_verifier):
        """The money test: iteration 1 -> real ParseError; iteration 2 -> fixed."""

        llm = FakeLLMPort([fenced(MODULE_BROKEN), fenced(MODULE_OK)])
        loop = BslAgentLoop(llm=llm, verifier=real_verifier)

        result = loop.run("Напиши процедуру, которая присваивает переменной значение")

        assert result.passed is True, "\n".join(result.summary_lines())
        assert len(result.iterations) == 2
        assert result.iterations[0].verified is False
        assert result.iterations[0].errors >= 1

        # real ParseError diagnostics reached the second LLM call
        feedback = llm.calls[1][1].content
        assert "ParseError" in feedback
        assert "Ошибка разбора" in feedback or "Ожидался" in feedback

        # and the verifier saw the exact extracted texts
        assert result.code == MODULE_OK

    def test_budget_exhausted_against_real_verifier(self, real_verifier):
        llm = FakeLLMPort([fenced(MODULE_BROKEN)] * 2)
        loop = BslAgentLoop(
            llm=llm, verifier=real_verifier, config=LoopConfig(max_iterations=2)
        )

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "budget_exhausted"
        assert len(result.iterations) == 2
        assert result.iterations[0].errors >= 1  # real diagnostics counted
        assert result.code == MODULE_BROKEN

    def test_iteration_log_carries_real_verify_ms(self, real_verifier):
        llm = FakeLLMPort([fenced(MODULE_OK)])
        loop = BslAgentLoop(llm=llm, verifier=real_verifier)

        result = loop.run("задача")

        assert result.passed is True
        assert result.iterations[0].verify_ms > 0  # real java run measured
