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
    extract_query_texts,
    normalize_output,
    normalize_query_text,
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

    def test_setup_with_semicolon_inside_string_stays_one_statement(self):
        checks = [
            ExecCheck(
                call='ПереводИзНСтр(Н, "ru")',
                setup='Н = "ru = \'Привет\'; en = \'Hello\'"',
            )
        ]
        driver = build_driver("Функция Ф()\nКонецФункции", checks)
        assert 'Н = "ru = \'Привет\'; en = \'Hello\'";' in driver

    def test_setup_splitter_respects_doubled_quotes(self):
        from harness_loop.executors import _split_setup_statements

        stmts = _split_setup_statements('А = "x"";y"; Б = 1; В = 2')
        assert stmts == ['А = "x"";y"', "Б = 1", "В = 2"]

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


class TestHttpStub:
    """Loopback stub server + driver prelude (http_stub checks)."""

    def test_driver_gets_port_prelude_when_server_port_given(self):
        checks = [ExecCheck(call="Ф()", expect="1", http_stub=True)]
        driver = build_driver("Функция Ф()\n    Возврат 1;\nКонецФункции", checks, server_port=8099)
        assert "ПортСервера = 8099;" in driver

    def test_driver_without_server_port_has_no_prelude(self):
        checks = [ExecCheck(call="Ф()", expect="1")]
        driver = build_driver("Функция Ф()\n    Возврат 1;\nКонецФункции", checks)
        assert "ПортСервера" not in driver

    def test_stub_routes_fixed_bodies_and_404(self):
        import urllib.request
        import urllib.error

        from harness_loop.executors import StubHttpServer

        with StubHttpServer() as stub:
            base = f"http://127.0.0.1:{stub.port}"
            assert urllib.request.urlopen(base + "/").read().decode() == "root-ok"
            assert urllib.request.urlopen(base + "/data").read().decode() == "payload-42"
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(base + "/missing")
            assert err.value.code == 404
            with pytest.raises(urllib.error.HTTPError) as unknown:
                urllib.request.urlopen(base + "/nope")
            assert unknown.value.code == 404

    def test_stub_flaky_drops_first_two_then_recovers(self):
        import urllib.request

        from harness_loop.executors import StubHttpServer

        with StubHttpServer() as stub:
            url = f"http://127.0.0.1:{stub.port}/flaky"
            drops = 0
            for _ in range(2):
                try:
                    urllib.request.urlopen(url, timeout=5).read()
                except Exception:
                    drops += 1  # connection reset/empty response — the client failure path
            assert drops == 2
            # from hit 3 the route recovers with the fixed body
            assert urllib.request.urlopen(url, timeout=5).read().decode() == "recovered-ok"

    def test_two_stubs_do_not_share_flaky_state(self):
        import urllib.request

        from harness_loop.executors import StubHttpServer

        first = StubHttpServer()
        try:
            url = f"http://127.0.0.1:{first.port}/flaky"
            try:
                urllib.request.urlopen(url, timeout=5).read()
            except Exception:
                pass  # drop #1 consumed
        finally:
            first.close()
        # a fresh server starts its hit counter from zero
        with StubHttpServer() as second:
            url = f"http://127.0.0.1:{second.port}/flaky"
            drops = 0
            for _ in range(2):
                try:
                    urllib.request.urlopen(url, timeout=5).read()
                except Exception:
                    drops += 1
            assert drops == 2

    def test_run_checks_starts_stub_only_for_http_checks(self, monkeypatch):
        import harness_loop.executors as ex

        started = []

        class _SpyStub:
            def __init__(self):
                started.append(1)
                self.port = 12345

            def close(self):
                pass

        monkeypatch.setattr(ex, "StubHttpServer", _SpyStub)

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("no engine on this machine")

        monkeypatch.setattr(ex.subprocess, "run", fake_run)
        runner = OneScriptRunner.__new__(OneScriptRunner)
        runner._binary = "oscript"
        runner._timeout = 15.0

        # no http_stub checks -> the stub server is never constructed
        runner.run_checks("Функция Ф()\nКонецФункции", [ExecCheck(call="Ф()", expect="1")])
        assert started == []
        # an http_stub check -> the stub is constructed (and closed in finally)
        http_probe = ExecCheck(call="Ф()", expect="1", http_stub=True)
        runner.run_checks("Функция Ф()\nКонецФункции", [http_probe])
        assert started == [1]


class TestQueryTextChecks:
    """Static query-text verification (roadmap 2.1 phase A: 5 query tasks)."""

    MODULE = (
        "Функция ТекстЗапросаНоменклатура()\n"
        "    Запрос = Новый Запрос;\n"
        '    Запрос.Текст =\n'
        '    "ВЫБРАТЬ\n'
        '    |    Номенклатура.Ссылка КАК Ссылка,\n'
        '    |ИЗ\n'
        '    |    Справочник.Номенклатура КАК Номенклатура";\n'
        "    Возврат Запрос.Текст;\n"
        "КонецФункции\n"
    )

    EXPECT = (
        "ВЫБРАТЬ\n"
        "|    Номенклатура.Ссылка КАК Ссылка,\n"
        "|ИЗ\n"
        "|    Справочник.Номенклатура КАК Номенклатура"
    )

    def test_extract_finds_query_literals(self):
        texts = extract_query_texts(self.MODULE)
        assert len(texts) == 1
        assert "ВЫБРАТЬ" in texts[0]
        assert "Справочник.Номенклатура" in texts[0]

    def test_extract_ignores_plain_strings(self):
        code = 'Сообщить("ВЫБРАТЬ нет тут");'  # ВЫБРАТЬ есть — квалифицируется
        assert len(extract_query_texts(code)) == 1
        code2 = 'А = "обычная строка"; Б = "ещё одна";'
        assert extract_query_texts(code2) == []

    def test_extract_unescapes_doubled_quotes(self):
        code = '"ВЫБРАТЬ поле ""Имя"" ИЗ Таблица"'
        texts = extract_query_texts(code)
        assert '""' not in texts[0]
        assert '"Имя"' in texts[0]

    def test_normalize_strips_pipes_and_case(self):
        norm = normalize_query_text("ВЫБРАТЬ\n|  А КАК Б\n|  ИЗ  С")
        assert norm == "выбрать а как б из с"

    def test_normalize_collapses_whitespace_runs(self):
        assert normalize_query_text("ВЫБРАТЬ    *   ИЗ\tТ") == "выбрать * из т"

    def test_empty_call_allowed_for_query_check(self):
        check = ExecCheck(query_text=True, expect="ВЫБРАТЬ")
        assert check.query_text is True

    def test_query_check_still_validates_call_type(self):
        with pytest.raises(ValueError, match="call"):
            ExecCheck(query_text=True, call=42, expect="")

    def test_pure_query_task_never_launches_engine(self, tmp_path, monkeypatch):
        # a binary that would explode if ever executed: the static path
        # must not spawn ANY process for query-only checks
        runner = OneScriptRunner(str(tmp_path / "no-such-oscript"))
        check = ExecCheck(query_text=True, expect=self.EXPECT)
        outcome = runner.run_checks(self.MODULE, [check])
        assert outcome.ran is True
        assert outcome.engine == "static-query"
        assert outcome.passed is True
        assert outcome.results[0].passed is True
        assert "номенклатура" in outcome.results[0].actual

    def test_pure_query_task_fails_when_literal_missing(self, tmp_path):
        runner = OneScriptRunner(str(tmp_path / "no-such-oscript"))
        check = ExecCheck(query_text=True, expect="ВЫБРАТЬ\n|ИЗ\n|    Другая.Таблица")
        outcome = runner.run_checks(self.MODULE, [check])
        assert outcome.passed is False
        assert "не построен" in outcome.results[0].error

    def test_driver_skips_query_blocks(self):
        runtime = ExecCheck(call="Ф(1)", expect="1")
        query = ExecCheck(query_text=True, expect="ВЫБРАТЬ")
        script = build_driver("Функция Ф()\nКонецФункции", [runtime, query])
        assert "__CHK1__" in script
        assert "__CHK2__" not in script  # query check has no driver block

    def test_mixed_results_keep_check_order(self, tmp_path):
        # engine outage branch: runtime results are empty, query verdicts
        # still surface (telemetry survives a dead engine)
        runner = OneScriptRunner(str(tmp_path / "no-such-oscript"))
        runtime = ExecCheck(call="Ф(1)", expect="1")
        query = ExecCheck(query_text=True, expect=self.EXPECT)
        outcome = runner.run_checks(self.MODULE, [runtime, query])
        assert outcome.ran is True
        assert outcome.passed is False  # engine failure dominates
        assert [r.call for r in outcome.results] == ["Ф(1)", "(текст запроса)"]
        assert outcome.results[1].passed is True  # static verdict computed anyway

    def test_merge_preserves_check_order(self):
        runtime = ExecCheck(call="Ф(1)", expect="1")
        query = ExecCheck(query_text=True, expect="ВЫБРАТЬ")
        rr = ["R1"]
        qr = ["Q1"]
        merged = OneScriptRunner._merge_results([runtime, query, runtime], rr * 2, qr)
        assert merged == ["R1", "Q1", "R1"]


class TestQueryCheckParsing:
    def test_query_check_without_call_loads(self, tmp_path):
        path = tmp_path / "tasks.yaml"
        path.write_text(
            "version: 1\ntasks:\n"
            "  - id: t1\n    prompt: задача\n"
            "    reference: |\n      Функция Ф()\n          Возврат 1;\n      КонецФункции\n"
            "    checks:\n"
            "      - query_text: true\n"
            '        expect: "ВЫБРАТЬ поле ИЗ Таблица"\n',
            encoding="utf-8",
        )
        from harness_loop.evals import load_tasks

        check = load_tasks(path)[0].checks[0]
        assert check.query_text is True
        assert check.call == ""
        assert "ВЫБРАТЬ" in check.expect

    def test_non_query_check_still_requires_call(self, tmp_path):
        path = tmp_path / "tasks.yaml"
        path.write_text(
            "version: 1\ntasks:\n"
            "  - id: t1\n    prompt: задача\n"
            "    checks:\n"
            '      - expect: "5"\n',
            encoding="utf-8",
        )
        from harness_loop.evals import load_tasks

        with pytest.raises(ValueError, match="call"):
            load_tasks(path)

    def test_bundled_query_tasks_carry_checks(self):
        from harness_loop.evals import bundled_tasks_path, load_tasks

        tasks = load_tasks(bundled_tasks_path())
        query_tasks = [t for t in tasks if t.category == "query"]
        assert len(query_tasks) == 5
        assert all(t.checks and t.checks[0].query_text for t in query_tasks)

    def test_bundled_coverage_reaches_ceiling(self):
        from harness_loop.evals import bundled_tasks_path, load_tasks

        tasks = load_tasks(bundled_tasks_path())
        with_checks = sum(1 for t in tasks if t.checks)
        # ceiling: 70 - 4 СКД - 2 struct (semantic divergence) = 64 (91%)
        assert with_checks == 64
