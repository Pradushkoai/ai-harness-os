"""CLI tests (bsl-check / bsl-doctor) with faked verification."""

from __future__ import annotations

import json

import pytest

from bsl_verify import cli
from bsl_verify.types import FileReport, VerifyResult
from conftest import MODULE_BROKEN, make_fake_run_analyze


def make_result(passed=True, errors=0, warnings=0) -> VerifyResult:
    return VerifyResult(
        files=[FileReport(path="module.bsl")],
        errors=errors,
        warnings=warnings,
        passed=passed,
        violations=[] if passed else ["errors: 1 > allowed 0"],
        policy="VerifyPolicy(max_errors=0)",
    )


class TestBslCheck:
    def test_check_passes_exit_zero(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            cli, "_run_check_pipeline", lambda args: make_result(passed=True)
        )
        code = cli.main_check([str(tmp_path)])
        assert code == 0
        assert "PASSED" in capsys.readouterr().out

    def test_check_fails_exit_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "_run_check_pipeline", lambda args: make_result(passed=False, errors=1)
        )
        code = cli.main_check(["x.bsl"])
        assert code == 1
        assert "FAILED" in capsys.readouterr().out

    def test_env_error_exit_two(self, monkeypatch, capsys):
        from bsl_verify.types import BslVerifyError

        def broken(_args):
            raise BslVerifyError("java not found")

        monkeypatch.setattr(cli, "_run_check_pipeline", broken)
        code = cli.main_check(["x.bsl"])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "_run_check_pipeline", lambda args: make_result(errors=2, passed=False)
        )
        code = cli.main_check(["x.bsl", "--json"])
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["counts"]["errors"] == 2

    def test_policy_flags_parsed(self):
        args = cli._build_parser().parse_args([
            "check", "m.bsl", "--max-errors", "3", "--max-warnings", "5",
            "--ignore", "ParseError,EmptyCodeBlock", "--only", "",
        ])
        policy = cli._policy_from_args(args)
        assert policy.max_errors == 3
        assert policy.max_warnings == 5
        assert policy.ignore_codes == frozenset({"ParseError", "EmptyCodeBlock"})
        assert policy.only_codes is None

    def test_end_to_end_with_fake_runner(self, monkeypatch, capsys, tmp_path):
        """Full pipeline: real CLI -> real verifier -> fake run_analyze."""

        from bsl_verify import verifier as verifier_mod

        monkeypatch.setattr(verifier_mod, "run_analyze", make_fake_run_analyze())
        module = tmp_path / "module_broken.bsl"
        module.write_text(MODULE_BROKEN, encoding="utf-8")

        code = cli.main_check([str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 1  # real report has 1 error -> policy fails
        assert "FAILED" in out

    def test_end_to_end_pass_with_relaxed_policy(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import verifier as verifier_mod

        monkeypatch.setattr(verifier_mod, "run_analyze", make_fake_run_analyze())
        module = tmp_path / "module_broken.bsl"
        module.write_text(MODULE_BROKEN, encoding="utf-8")

        code = cli.main_check([str(tmp_path), "--ignore", "ParseError"])
        out = capsys.readouterr().out
        assert code == 0
        assert "PASSED" in out


class TestBslDoctor:
    def test_all_missing_exit_two(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import runner

        monkeypatch.delenv("BSL_JAVA", raising=False)
        monkeypatch.delenv("JAVA_HOME", raising=False)
        monkeypatch.delenv("BSL_LS_JAR", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(runner.Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)  # no local jar

        code = cli.main_doctor([])
        out = capsys.readouterr().out
        assert code == 2
        assert "NOT FOUND" in out
        assert "releases" in out  # download hint

    def test_all_present_exit_zero(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import runner

        java = tmp_path / "java"
        java.write_text("x")
        jar = tmp_path / "bsl-language-server.jar"
        jar.write_bytes(b"x" * 1024)

        monkeypatch.delenv("BSL_JAVA", raising=False)
        monkeypatch.delenv("JAVA_HOME", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda name: str(java))
        monkeypatch.setenv("BSL_LS_JAR", str(jar))
        monkeypatch.setattr(cli, "java_version", lambda j: "openjdk 21")

        code = cli.main_doctor([])
        out = capsys.readouterr().out
        assert code == 0
        assert "ready to verify" in out
        assert "openjdk 21" in out


class TestVersion:
    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli._main(["--version"])
        assert excinfo.value.code == 0
        assert "bsl-check" in capsys.readouterr().out


class TestConsoleEntryPoints:
    def test_main_check_reads_sys_argv(self, monkeypatch, capsys, tmp_path):
        """Console scripts call main_check() with NO arguments — it must
        read sys.argv itself (regression: 'paths' went missing)."""

        from bsl_verify import verifier as verifier_mod

        monkeypatch.setattr(verifier_mod, "run_analyze", make_fake_run_analyze())
        module = tmp_path / "m.bsl"
        module.write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8")

        monkeypatch.setattr("sys.argv", ["bsl-check", str(module)])
        code = cli.main_check()  # no args, like a real console script
        assert code in (0, 1)
        assert "BSL check" in capsys.readouterr().out

    def test_main_doctor_reads_sys_argv(self, monkeypatch, capsys, tmp_path):
        from bsl_verify import runner

        monkeypatch.setattr("sys.argv", ["bsl-doctor"])
        monkeypatch.delenv("BSL_JAVA", raising=False)
        monkeypatch.delenv("JAVA_HOME", raising=False)
        monkeypatch.delenv("BSL_LS_JAR", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(runner.Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        code = cli.main_doctor()
        assert code == 2
        assert "NOT FOUND" in capsys.readouterr().out
