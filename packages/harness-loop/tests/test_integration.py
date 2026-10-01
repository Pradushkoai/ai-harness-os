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


class TestBenchmarkReferences:
    """Every reference solution must be clean under the REAL bsl LS.

    The benchmark's credibility rests on gold solutions that verify with
    zero Error diagnostics — otherwise a model generating the reference
    verbatim would still fail the eval. Default policy: max_errors=0.
    One JVM run for the whole set: all references staged into a single
    directory, one `verify_dir` call (~20s instead of ~7s per file).
    """

    def test_all_reference_solutions_pass_real_verifier(
        self, real_verifier, tmp_path
    ):
        from harness_loop.evals import bundled_tasks_path, load_tasks

        tasks = load_tasks(bundled_tasks_path())
        staging = tmp_path / "references"
        staging.mkdir()
        for task in tasks:
            (staging / f"{task.id}.bsl").write_text(
                task.reference, encoding="utf-8"
            )

        result = real_verifier.verify_dir(str(staging))

        by_name = {}
        for report in result.files:
            name = str(report.path).replace("\\", "/").rsplit("/", 1)[-1]
            by_name[name] = report

        failures = []
        for task in tasks:
            report = by_name.get(f"{task.id}.bsl")
            if report is None:
                failures.append(f"{task.id}: missing from the verifier report")
                continue
            error_messages = [
                str(d.message)
                for d in report.diagnostics
                if str(d.severity).endswith("Error")
            ]
            if error_messages:
                failures.append(f"{task.id}: {error_messages[:2]}")
        assert not failures, "reference solutions with errors:\n" + "\n".join(failures)

    def test_bundled_reference_count(self):
        from harness_loop.evals import bundled_tasks_path, load_tasks

        assert len(load_tasks(bundled_tasks_path())) == 30
