"""CLI tests (harness-loop run / doctor) with faked LLM and verifier."""

from __future__ import annotations

import argparse
import json

import pytest
from russian_llm_pack import RLLError

from harness_loop import cli
from harness_loop.types import LoopResult

from conftest import MODULE_OK, FakeLLMPort, FakeVerifier, fenced, make_verify_result


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


@pytest.fixture
def patched_judge(monkeypatch):
    """Replace judge construction with a scripted fake (PASS by default)."""

    holder = {}
    judge_llm = FakeLLMPort(["VERDICT: PASS\nSCORE: 9\nREASONING: ok"])
    holder["judge_llm"] = judge_llm

    def fake_build_judge(args):
        from harness_loop.judge import Judge

        return Judge(judge_llm)

    monkeypatch.setattr(cli, "_build_judge", fake_build_judge)
    return holder


@pytest.fixture
def patched_eval_factories(monkeypatch):
    """LLM/verifier with plenty of OK responses (eval runs many tasks)."""

    llm = FakeLLMPort([fenced(MODULE_OK)] * 80)
    verifier = FakeVerifier([make_verify_result(passed=True)] * 80)
    monkeypatch.setattr(cli, "_build_llm", lambda args: llm)
    monkeypatch.setattr(cli, "_build_verifier", lambda args: verifier)
    return {"llm": llm, "verifier": verifier}


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


class TestRunJudge:
    def test_judge_pass_exit_zero(self, patched_factories, patched_judge, capsys):
        code = cli.main(["run", "задача", "--judge"])
        out = capsys.readouterr().out

        assert code == 0
        assert "judge: PASS" in out
        assert "score 9" in out

    def test_judge_not_built_without_flag(self, patched_factories, monkeypatch):
        built = []

        def spy_build_judge(args):
            built.append(args)
            raise AssertionError("must not be called without --judge")

        monkeypatch.setattr(cli, "_build_judge", spy_build_judge)
        code = cli.main(["run", "задача"])

        assert code == 0
        assert built == []

    def test_judge_veto_exit_one(self, patched_factories, patched_judge, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "_build_llm",
            lambda args: FakeLLMPort([fenced(MODULE_OK)] * 3),
        )
        monkeypatch.setattr(
            cli, "_build_verifier",
            lambda args: FakeVerifier([make_verify_result(passed=True)] * 3),
        )
        patched_judge["judge_llm"].responses = ["VERDICT: FAIL\nISSUES:\n- x\n"] * 3

        code = cli.main(["run", "задача", "--judge", "--max-iterations", "3"])
        out = capsys.readouterr().out

        assert code == 1
        assert "judge_rejected" in out

    def test_judge_json_payload(self, patched_factories, patched_judge, capsys):
        code = cli.main(["run", "задача", "--judge", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["judge"]["approved"] is True
        assert payload["judge"]["score"] == 9
        assert payload["totals"]["judge_ms"] == 12.5

    def test_judge_verbose_progress(self, patched_factories, patched_judge, capsys):
        code = cli.main(["run", "задача", "--judge", "--verbose"])
        err = capsys.readouterr().err

        assert code == 0
        assert "judge PASS" in err


class TestRunLangfuse:
    def test_langfuse_without_keys_is_safe_noop(self, patched_factories, monkeypatch, capsys):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        code = cli.main(["run", "задача", "--langfuse"])

        assert code == 0
        assert "PASSED" in capsys.readouterr().out


class TestEval:
    def test_eval_bundled_limit_two(self, patched_eval_factories, capsys):
        code = cli.main(["eval", "--limit", "2"])
        out = capsys.readouterr().out

        assert code == 0
        assert "BSL eval: 2/2 resolved (100%)" in out
        assert "[PASS] func-sum-two-numbers" in out

    def test_eval_json_and_reports(self, patched_eval_factories, tmp_path, capsys):
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        code = cli.main([
            "eval",
            "--save-report", str(report_json),
            "--markdown", str(report_md),
            "--json",
        ])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["total"] == 70  # v0.4 benchmark size
        assert payload["resolved"] == 70
        assert payload["by_difficulty"]["hard"]["total"] == 18
        assert payload["by_category"]["table"]["total"] == 6
        assert payload["by_category"]["skd"]["total"] == 4
        assert report_json.is_file()
        assert "Mini SWE-bench-BSL" in report_md.read_text(encoding="utf-8")
        assert "По категориям" in report_md.read_text(encoding="utf-8")

    def test_eval_bad_tasks_file_exit_two(self, patched_factories, capsys):
        code = cli.main(["eval", "--tasks", "нет_такого_файла.yaml"])

        assert code == 2
        assert "error" in capsys.readouterr().err

    def test_eval_bad_limit_exit_two(self, patched_factories, capsys):
        code = cli.main(["eval", "--limit", "0"])

        assert code == 2

    def test_eval_with_judge(self, patched_eval_factories, patched_judge, capsys):
        patched_judge["judge_llm"].responses = [
            "VERDICT: PASS\nSCORE: 8\nREASONING: ок"
        ] * 10

        code = cli.main(["eval", "--limit", "2", "--judge"])
        out = capsys.readouterr().out

        assert code == 0
        assert "2/2 resolved" in out

    def test_eval_judge_reference_mode_by_default(
        self, patched_eval_factories, patched_judge, capsys
    ):
        """Default: the judge sees gold solutions (reference-aware L2)."""
        patched_judge["judge_llm"].responses = [
            "VERDICT: PASS\nSCORE: 8\nREASONING: ок"
        ] * 10

        code = cli.main(["eval", "--limit", "2", "--judge", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["judge"]["mode"] == "reference"
        # the judge got the etalon of the first bundled task
        judge_user = patched_judge["judge_llm"].calls[0][1].content
        assert "Эталонное решение" in judge_user

    def test_eval_no_judge_reference_flag(
        self, patched_eval_factories, patched_judge, capsys
    ):
        """--no-judge-reference: plain judging as in v0.4 (A/B comparison)."""
        patched_judge["judge_llm"].responses = [
            "VERDICT: PASS\nSCORE: 8\nREASONING: ок"
        ] * 10

        code = cli.main([
            "eval", "--limit", "2", "--judge",
            "--no-judge-reference", "--json",
        ])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["judge"]["mode"] == "plain"
        judge_user = patched_judge["judge_llm"].calls[0][1].content
        assert "Эталонное решение" not in judge_user

    def test_eval_judge_reference_never_leaks_to_generator(
        self, patched_eval_factories, patched_judge, capsys
    ):
        """Anti-cheating at the CLI level: etalon never enters generator prompts."""
        patched_judge["judge_llm"].responses = [
            "VERDICT: PASS\nSCORE: 8\nREASONING: ок"
        ] * 10

        cli.main(["eval", "--limit", "2", "--judge"])

        generator = patched_eval_factories["llm"]
        for messages in generator.calls:
            for message in messages:
                assert "Эталонное решение" not in message.content

    def test_eval_judge_stats_in_json_and_markdown(
        self, patched_eval_factories, patched_judge, capsys, tmp_path
    ):
        patched_judge["judge_llm"].responses = [
            "VERDICT: PASS\nSCORE: 8\nREASONING: ок"
        ] * 10
        report_md = tmp_path / "report.md"

        code = cli.main([
            "eval", "--limit", "2", "--judge",
            "--markdown", str(report_md), "--json",
        ])
        payload = json.loads(capsys.readouterr().out)
        markdown = report_md.read_text(encoding="utf-8")

        assert code == 0
        assert payload["judge"]["judged"] == 2
        assert payload["judge"]["approved"] == 2
        assert payload["judge"]["avg_score"] == 8.0
        assert payload["tasks"][0]["judge_score"] == 8
        assert "## Ревьюер (L2)" in markdown
        assert "против эталонов" in markdown

    def test_eval_category_filter(self, patched_eval_factories, capsys):
        code = cli.main(["eval", "--category", "table", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["total"] == 6
        assert all(t["category"] == "table" for t in payload["tasks"])

    def test_eval_difficulty_filter(self, patched_eval_factories, capsys):
        code = cli.main(["eval", "--difficulty", "hard", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["total"] == 18  # v0.4: 13 -> 18
        assert all(t["difficulty"] == "hard" for t in payload["tasks"])

    def test_eval_multi_value_filter(self, patched_eval_factories, capsys):
        code = cli.main(["eval", "--category", "table,numbers", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["total"] == 10

    def test_eval_filter_applies_before_limit(self, patched_eval_factories, capsys):
        """--difficulty hard --limit 3 = first 3 HARD tasks, not first 3 tasks."""

        code = cli.main(["eval", "--difficulty", "hard", "--limit", "3", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["total"] == 3
        assert [t["difficulty"] for t in payload["tasks"]] == ["hard"] * 3
        assert payload["tasks"][0]["id"] == "func-fibonacci"

    def test_eval_filter_no_match_exit_two(self, patched_eval_factories, capsys):
        code = cli.main(["eval", "--category", "нет_такой"])

        assert code == 2
        assert "no tasks match" in capsys.readouterr().err

    def test_eval_empty_filter_value_exit_two(self, patched_eval_factories, capsys):
        code = cli.main(["eval", "--category", "   "])

        assert code == 2
        assert "at least one value" in capsys.readouterr().err

    def test_eval_version_subparser(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli._main(["eval", "--version"])
        assert excinfo.value.code == 0

    # -- phase E: the eval path gets the same context socket as `run` --

    def test_eval_context_project_feeds_generator(self, patched_eval_factories, tmp_path):
        """--context-project: indexed context reaches the generator prompt."""

        project = tmp_path / "project"
        (project / "CommonModules").mkdir(parents=True)
        (project / "CommonModules" / "ЦеныБарахла.bsl").write_text(
            "Функция ЦенаТовара(Товар) Экспорт\n    Возврат 0;\nКонецФункции\n",
            encoding="utf-8",
        )
        llm = patched_eval_factories["llm"]

        code = cli.main([
            "eval",
            "--limit", "1",
            "--context-project", str(project),
        ])
        assert code == 0
        # context lands in the USER message (task prompt), not the system one
        user_prompt = llm.calls[0][1].content if len(llm.calls[0]) > 1 else ""
        assert "Контекст проекта" in user_prompt
        assert "ЦеныБарахла" in user_prompt  # the indexed module reached the prompt

    def test_eval_context_project_missing_dir_is_not_a_crash(
        self, patched_eval_factories, capsys
    ):
        code = cli.main(["eval", "--limit", "1", "--context-project", "/no/such/dir"])

        assert code == 0  # the provider reports a note; eval keeps going

    def test_eval_context_disabled_env(
        self, patched_eval_factories, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("HARNESS_CONTEXT", "none")
        code = cli.main([
            "eval",
            "--limit", "1",
            "--context-project", str(tmp_path),
        ])

        assert code == 0
        assert "HARNESS_CONTEXT=none" in capsys.readouterr().err


class TestDoctorLangfuse:
    def test_doctor_mentions_langfuse(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import runner as bsl_runner

        monkeypatch.delenv("RLP_CONFIG", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        for var in ("BSL_JAVA", "JAVA_HOME", "BSL_LS_JAR"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(bsl_runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(bsl_runner.Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        code = cli.main(["doctor"])
        out = capsys.readouterr().out

        assert code == 2
        assert "langfuse" in out.lower()


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


class TestVerifierPolicyDefaults:
    """LS false positives merged into --ignore unless --strict-verify."""

    def _args(self, **overrides):
        base = {
            "ignore": "",
            "max_errors": 0,
            "strict_verify": False,
            "java": None,
            "jar": None,
            "timeout": 0.1,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_default_policy_ignores_ls_false_positives(self):
        verifier = cli._build_verifier(self._args())
        assert "InvalidCharacterInFile" in verifier.policy.ignore_codes

    def test_strict_verify_keeps_raw_ls_verdict(self):
        verifier = cli._build_verifier(self._args(strict_verify=True))
        assert "InvalidCharacterInFile" not in verifier.policy.ignore_codes

    def test_user_ignore_merged_without_losing_own_codes(self):
        verifier = cli._build_verifier(self._args(ignore="MyCode , Other"))
        codes = verifier.policy.ignore_codes
        assert {"MyCode", "Other", "InvalidCharacterInFile"} <= set(codes)

    def test_run_and_eval_parsers_expose_strict_verify(self):
        parser = cli._build_parser()
        run_args = parser.parse_args(
            ["run", "задача", "--strict-verify"]
        )
        assert run_args.strict_verify is True
        eval_args = parser.parse_args(["eval", "--strict-verify"])
        assert eval_args.strict_verify is True


class TestJudgeSamplesCLI:
    """--judge-samples K reaches the judge config."""

    def _args(self, **overrides):
        base = {
            "config": None,
            "judge_chain": None,
            "judge_model": None,
            "judge_samples": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def _fake_router_port(self, monkeypatch):
        from harness_loop.ports import RouterPort  # noqa: F401 — shape only

        class FakePort:
            def complete(self, messages, **kwargs):
                raise AssertionError("not called in this test")

        port = FakePort()
        return port

    def test_default_judge_config_samples_one(self, monkeypatch):
        # Router/RouterConfig are irrelevant here: patch them out entirely.
        monkeypatch.setattr(cli, "load_config", lambda: (object(), "builtin"))
        monkeypatch.setattr(
            cli, "Router", type("R", (), {"from_config": staticmethod(lambda rc: None)})
        )

        class FakeRouterPort:
            def __init__(self, router, task=None, model=None):
                pass

        monkeypatch.setattr(cli, "RouterPort", FakeRouterPort)
        judge = cli._build_judge(self._args())
        assert judge.config.samples == 1

    def test_judge_samples_flag_reaches_config(self, monkeypatch):
        monkeypatch.setattr(cli, "load_config", lambda: (object(), "builtin"))
        monkeypatch.setattr(
            cli, "Router", type("R", (), {"from_config": staticmethod(lambda rc: None)})
        )

        class FakeRouterPort:
            def __init__(self, router, task=None, model=None):
                pass

        monkeypatch.setattr(cli, "RouterPort", FakeRouterPort)
        judge = cli._build_judge(self._args(judge_samples=3))
        assert judge.config.samples == 3

    def test_eval_parser_exposes_judge_samples(self):
        parser = cli._build_parser()
        args = parser.parse_args(["eval", "--judge-samples", "3"])
        assert args.judge_samples == 3
