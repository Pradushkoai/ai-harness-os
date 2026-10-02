"""Unit tests for the L1 execution oracle (executors.py).

Live-engine behaviour is covered by test_integration.py (skipped when
oscript is not installed); everything here runs without the world:
driver construction, output parsing, normalization, env scrubbing and
discovery wiring are pure or monkeypatched.
"""

from __future__ import annotations

import pytest

from harness_loop.executors import (
    ExecCheck,
    OneScriptRunner,
    build_driver,
    normalize_output,
    parse_driver_output,
    sanitize_env,
)


class TestExecCheck:
    def test_rejects_empty_call(self):
        with pytest.raises(ValueError, match="call"):
            ExecCheck(call="   ")

    def test_rejects_non_string_expect(self):
        with pytest.raises(ValueError, match="expect"):
            ExecCheck(call="Ф(1)", expect=42)

    def test_empty_expect_is_allowed(self):
        check = ExecCheck(call="ВерниНеопределено()", expect="")
        assert check.expect == ""
        assert check.case_fold is False


class TestNormalizeOutput:
    def test_crlf_and_trailing_spaces(self):
        assert normalize_output("5 \r\n\r\n") == "5"

    def test_blank_trailing_lines_dropped(self):
        assert normalize_output("a\nb\n\n\n") == "a\nb"

    def test_inner_blank_lines_kept(self):
        assert normalize_output("a\n\nb") == "a\n\nb"

    def test_case_fold_collapses_whitespace(self):
        assert normalize_output("ВЫБРАТЬ  * Из  Т", case_fold=True) == "выбрать * из т"

    def test_case_fold_lowercases_and_strips(self):
        assert normalize_output("  Строка \t Текста ", case_fold=True) == "строка текста"

    def test_exact_mode_keeps_case(self):
        assert normalize_output("Абв") == "Абв"


class TestSanitizeEnv:
    def test_drops_secret_looking_vars(self):
        env = {
            "PATH": "/usr/bin",
            "QWEN_API_KEY": "sk-xxx",
            "DEEPSEEK_TOKEN": "t",
            "MY_PASSWORD": "p",
            "GITHUB_CREDENTIALS": "c",
            "SAFE_VAR": "1",
        }
        clean = sanitize_env(env)
        assert clean == {"PATH": "/usr/bin", "SAFE_VAR": "1"}

    def test_cyrillic_and_system_vars_survive(self):
        env = {"SYSTEMROOT": "C:\\Windows", "ПЕРЕМ": "зн"}
        assert sanitize_env(env) == env


class TestBuildDriver:
    def test_contains_module_and_markers(self):
        module = "Функция Сумма(А, Б)\n    Возврат А + Б;\nКонецФункции"
        checks = [ExecCheck(call="Сумма(2, 3)", expect="5")]
        driver = build_driver(module, checks)
        assert "Функция Сумма(А, Б)" in driver
        assert '__CHK1__' in driver
        assert "РезультатВызова = Сумма(2, 3);" in driver
        assert "Попытка" in driver and "КонецПопытки;" in driver
        assert "__HARNESS_DRIVER__" in driver

    def test_each_check_gets_own_try_block_and_marker(self):
        checks = [
            ExecCheck(call="Ф(1)", expect="a"),
            ExecCheck(call="Ф(2)", expect="b"),
        ]
        driver = build_driver("Код", checks)
        assert driver.count("Попытка") == 2
        assert "__CHK1__" in driver and "__CHK2__" in driver

    def test_empty_check_list_still_valid_script(self):
        driver = build_driver("Код", [])
        assert "Код" in driver
        assert "__CHK" not in driver


class TestParseDriverOutput:
    def test_single_check_value(self):
        out = "__CHK1__\n5\n"
        assert parse_driver_output(out, 1) == ["5"]

    def test_multi_check_values(self):
        out = "шум модуля\n__CHK1__\nда\n__CHK2__\n\nдве строки\n\n"
        assert parse_driver_output(out, 2) == ["да", "\nдве строки\n"]

    def test_missing_marker_is_none(self):
        assert parse_driver_output("модуль упал", 1) == [None]

    def test_marker_mid_value_starts_new_capture(self):
        out = "__CHK1__\nx\n__CHK2__\ny\n"
        assert parse_driver_output(out, 2) == ["x", "y"]

    def test_crlf_tolerant(self):
        assert parse_driver_output("__CHK1__\r\n5\r\n", 1) == ["5"]


class TestOneScriptRunnerDiscovery:
    def test_explicit_path_missing_returns_none_no_fallback(self, monkeypatch):
        monkeypatch.setenv("OSCRIPT_PATH", "/nonexistent/oscript")
        assert OneScriptRunner.discover() is None

    def test_explicit_exe_suffix_accepted_on_any_platform(self, monkeypatch, tmp_path):
        # Windows-style path: cannot be stat'ed on POSIX, but .exe suffix
        # means "user pointed at a real engine" — accept it.
        monkeypatch.setenv("OSCRIPT_PATH", "C:\\tools\\oscript\\bin\\oscript.exe")
        runner = OneScriptRunner.discover()
        assert runner is not None
        assert "oscript.exe" in runner._binary

    def test_explicit_existing_file(self, monkeypatch, tmp_path):
        binary = tmp_path / "oscript"
        binary.write_text("#!/bin/sh\n")
        monkeypatch.setenv("OSCRIPT_PATH", str(binary))
        runner = OneScriptRunner.discover()
        assert runner is not None and runner.available()

    def test_which_on_path(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OSCRIPT_PATH", raising=False)
        monkeypatch.delenv("OSCRIPT_HOME", raising=False)
        exe = tmp_path / "oscript"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        monkeypatch.setattr("shutil.which", lambda name: str(exe) if name == "oscript" else None)
        runner = OneScriptRunner.discover()
        assert runner is not None

    def test_home_bin_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OSCRIPT_PATH", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        home = tmp_path / "oscript-home"
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "oscript").write_text("#!/bin/sh\n")
        monkeypatch.setenv("OSCRIPT_HOME", str(home))
        runner = OneScriptRunner.discover()
        assert runner is not None and runner.available()

    def test_nowhere_returns_none(self, monkeypatch):
        monkeypatch.delenv("OSCRIPT_PATH", raising=False)
        monkeypatch.delenv("OSCRIPT_HOME", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert OneScriptRunner.discover() is None


class TestOutcomeFromStdout:
    """_outcome_from_stdout is the verdict logic — test it directly."""

    def _runner(self, tmp_path):
        binary = tmp_path / "oscript"
        binary.write_text("#!/bin/sh\n")
        return OneScriptRunner(str(binary))

    def test_pass_fail_and_error_paths(self, tmp_path):
        runner = self._runner(tmp_path)
        checks = [
            ExecCheck(call="Сумма(2,3)", expect="5"),
            ExecCheck(call="Сумма(2,2)", expect="5"),
        ]
        stdout = "__CHK1__\n5\n__CHK2__\n4\n"
        outcome = runner._outcome_from_stdout(stdout, checks, 1.0)
        assert outcome.ran and outcome.passed is False
        assert outcome.results[0].passed is True
        assert outcome.results[1].passed is False
        assert outcome.results[1].actual == "4"

    def test_runtime_error_fails_check_with_message(self, tmp_path):
        runner = self._runner(tmp_path)
        checks = [ExecCheck(call="Бум()", expect="1")]
        stdout = "__CHK1__\n__ERROR__: Divide by zero\n"
        outcome = runner._outcome_from_stdout(stdout, checks, 1.0)
        assert outcome.passed is False
        assert "Divide by zero" in outcome.results[0].error

    def test_case_fold_check_ignores_keyword_case(self, tmp_path):
        runner = self._runner(tmp_path)
        checks = [
            ExecCheck(call="ТекстЗапроса()", expect="ВЫБРАТЬ * Из Справочник.Т", case_fold=True)
        ]
        stdout = "__CHK1__\nвыбрать  *  из  справочник.т\n"
        outcome = runner._outcome_from_stdout(stdout, checks, 1.0)
        assert outcome.passed is True

    def test_missing_marker_marks_check_failed(self, tmp_path):
        runner = self._runner(tmp_path)
        checks = [ExecCheck(call="Ф()", expect="1")]
        outcome = runner._outcome_from_stdout("модуль умер", checks, 1.0)
        assert outcome.passed is False
        assert "маркер" in outcome.results[0].error
