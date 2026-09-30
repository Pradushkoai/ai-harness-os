"""CLI tests (harness-loop run / doctor) with faked LLM and verifier."""

from __future__ import annotations

import json

import pytest
from russian_llm_pack import RLLError

from harness_loop import cli
from harness_loop.types import LoopResult

from conftest import MODULE_BROKEN, MODULE_OK, FakeLLMPort, FakeVerifier, fenced, make_verify_result


def make_loop_result(passed=True, failure_reason="", code="Процедура А()\nКонецПроцедуры\n"):
    return LoopResult(
        passed=passed,
        code=code,
        iterations=[],
        failure_reason=failure_reason,
        error="взрыв" if failure_reason else None,
    )


@pytest.fixture
def patched_factories(monkeypatch):
    """Replace LLM/verifier construction with fakes; exposes them to the test."""

    holder = {}

    def fake_build_llm(args):
        holder["llm"] = FakeLLMPort([fenced(MODULE_OK)])
        return holder["llm"]

    def fake_build_verifier(args):
        holder["verifier"] = FakeVerifier([make_verify_result(passed=True)])
        return holder["verifier"]

    monkeypatch.setattr(cli, "_build_llm", fake_build_llm)
    monkeypatch.setattr(cli, "_build_verifier", fake_build_verifier)
    return holder


class TestRun:
    def test_passed_exit_zero(self, patched_factories, capsys):
        code = cli.main(["run", "Напиши процедуру"])
        out = capsys.readouterr().out

        assert code == 0
        assert "PASSED" in out
        assert "```bsl" in out  # final code rendered

    def test_budget_exhausted_exit_one(self, patched_factories, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "_build_llm", lambda args: FakeLLMPort(["отказываюсь"] * 3)
        )
        monkeypatch.setattr(
            cli, "_build_verifier", lambda args: FakeVerifier([])
        )

        code = cli.main(["run", "задача"])
        out = capsys.readouterr().out

        assert code == 1
        assert "budget_exhausted" in out

    def test_llm_error_exit_two(self, patched_factories, monkeypatch, capsys):
        class NoModel(FakeLLMPort):
            def complete(self, messages, *, model=None, **kwargs):
                raise RLLError("no available model")

        monkeypatch.setattr(cli, "_build_llm", lambda args: NoModel([]))
        monkeypatch.setattr(cli, "_build_verifier", lambda args: FakeVerifier([]))

        code = cli.main(["run", "задача"])

        assert code == 2

    def test_verifier_error_exit_two(self, patched_factories, monkeypatch, capsys):
        from bsl_verify.types import BslVerifyError

        class NoJava(FakeVerifier):
            def verify_module_text(self, text, *, filename="module.bsl"):
                raise BslVerifyError("java not found")

        monkeypatch.setattr(
            cli, "_build_llm", lambda args: FakeLLMPort([fenced(MODULE_OK)])
        )
        monkeypatch.setattr(cli, "_build_verifier", lambda args: NoJava([]))

        code = cli.main(["run", "задача"])

        assert code == 2

    def test_json_output(self, patched_factories, capsys):
        code = cli.main(["run", "задача", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["passed"] is True
        assert "iterations" in payload
        assert "code" in payload

    def test_save_writes_final_code(self, patched_factories, capsys, tmp_path):
        target = tmp_path / "module.bsl"

        code = cli.main(["run", "задача", "--save", str(target)])
        out = capsys.readouterr().out

        assert code == 0
        assert f"saved: {target}" in out
        assert target.read_text(encoding="utf-8") == MODULE_OK

    def test_verbose_progress_to_stderr(self, patched_factories, capsys):
        code = cli.main(["run", "задача", "--verbose"])
        captured = capsys.readouterr()

        assert code == 0
        assert "iteration 1/3" in captured.err

    def test_context_conflict_is_usage_error(self, patched_factories, capsys):
        code = cli.main(["run", "задача", "--context", "текст", "--context-file", "x.txt"])

        assert code == 2
        assert "either --context or --context-file" in capsys.readouterr().err

    def test_missing_context_file_is_usage_error(self, patched_factories, capsys):
        code = cli.main(["run", "задача", "--context-file", "нет_такого_файла.txt"])

        assert code == 2
        assert "not found" in capsys.readouterr().err

    def test_context_file_content_reaches_llm(self, patched_factories, capsys, tmp_path):
        ctx = tmp_path / "context.md"
        ctx.write_text("Конфигурация УТ 11.5", encoding="utf-8")

        cli.main(["run", "задача", "--context-file", str(ctx)])

        user_prompt = patched_factories["llm"].calls[0][1].content
        assert "Конфигурация УТ 11.5" in user_prompt


class TestVersion:
    def test_root_version(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli._main(["--version"])
        assert excinfo.value.code == 0
        assert "harness-loop" in capsys.readouterr().out

    def test_run_subparser_version(self, capsys):
        """`harness-loop run --version` — the bsl-check regression, covered day one."""

        with pytest.raises(SystemExit) as excinfo:
            cli._main(["run", "--version"])
        assert excinfo.value.code == 0
        assert "harness-loop" in capsys.readouterr().out

    def test_console_script_version(self, capsys, monkeypatch):
        """The REAL entry point path: main() reading sys.argv itself."""

        monkeypatch.setattr("sys.argv", ["harness-loop", "--version"])
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 0


class TestDoctor:
    def test_everything_missing_exit_two(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import runner as bsl_runner

        monkeypatch.delenv("RLP_CONFIG", raising=False)
        for var in ("DEEPSEEK_API_KEY", "ZAI_API_KEY", "GIGACHAT_*", "YANDEXGPT_API_KEY",
                    "BSL_JAVA", "JAVA_HOME", "BSL_LS_JAR"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(bsl_runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(bsl_runner.Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        code = cli.main(["doctor"])
        out = capsys.readouterr().out

        assert code == 2
        assert "NOT FOUND" in out
        assert "DEEPSEEK_API_KEY" in out  # actionable LLM hint
        assert "INCOMPLETE" in out

    def test_no_args_reads_sys_argv(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import runner as bsl_runner

        monkeypatch.delenv("RLP_CONFIG", raising=False)
        for var in ("BSL_JAVA", "JAVA_HOME", "BSL_LS_JAR"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(bsl_runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(bsl_runner.Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["harness-loop", "doctor"])

        assert cli.main() == 2
        assert "INCOMPLETE" in capsys.readouterr().out
