"""A2/A3 tests: checks in the task format + L0/L1/L2 report metrics."""

from __future__ import annotations

from dataclasses import replace

import pytest

from harness_loop.evals import BslTask, EvalReport, TaskOutcome, load_tasks, run_eval
from harness_loop.executors import (
    CheckResult,
    ExecCheck,
    ExecOutcome,
    normalize_output,
)
from harness_loop.types import IterationLog, LoopResult


def _task(checks=(), category="function"):
    return BslTask(
        id="t1",
        prompt="задача",
        reference="Функция Ф()\n    Возврат 1;\nКонецФункции",
        category=category,
        checks=tuple(checks),
    )


def _result(passed=True, code="Функция Ф()\n    Возврат 1;\nКонецФункции"):
    return LoopResult(passed=passed, code=code, failure_reason="" if passed else "budget_exhausted")


class FakeExecutor:
    """Scripted executor: maps (module, call) -> printed value or None."""

    name = "fake"

    def __init__(self, outputs=None, unavailable=False, ran=True):
        self._outputs = outputs or {}
        self._unavailable = unavailable
        self._ran = ran
        self.calls = []

    def available(self):
        return not self._unavailable

    def run_checks(self, module_code, checks, timeout=15.0):
        self.calls.append((module_code, list(checks)))
        if not self._ran:
            return ExecOutcome(ran=False, engine=self.name)
        results = []
        for check in checks:
            actual = self._outputs.get(check.call, "__MISSING__")
            passed = normalize_output(actual, check.case_fold) == normalize_output(
                check.expect, check.case_fold
            )
            results.append(
                CheckResult(
                    call=check.call,
                    expect=check.expect,
                    actual=actual,
                    passed=passed,
                )
            )
        return ExecOutcome(
            ran=True,
            engine=self.name,
            passed=all(r.passed for r in results),
            results=results,
        )


class TestLoadChecks:
    def _write(self, tmp_path, body):
        path = tmp_path / "tasks.yaml"
        path.write_text(
            "version: 1\ntasks:\n  - id: t1\n    prompt: задача\n"
            f"    reference: |\n      Функция Ф()\n{body}",
            encoding="utf-8",
        )
        return path

    def test_checks_parsed_and_validated(self, tmp_path):
        path = self._write(
            tmp_path,
            "    checks:\n"
            "      - call: \"Ф(2, 3)\"\n"
            "        expect: \"5\"\n"
            "      - call: \"Текст()\"\n"
            "        expect: \"ВЫБРАТЬ\"\n"
            "        case_fold: true\n",
        )
        tasks = load_tasks(path)
        assert len(tasks[0].checks) == 2
        assert tasks[0].checks[0].call == "Ф(2, 3)"
        assert tasks[0].checks[0].expect == "5"
        assert tasks[0].checks[0].case_fold is False
        assert tasks[0].checks[1].case_fold is True

    def test_empty_expect_allowed(self, tmp_path):
        path = self._write(
            tmp_path, "    checks:\n      - call: \"Н()\"\n        expect: \"\"\n"
        )
        assert load_tasks(path)[0].checks[0].expect == ""

    def test_missing_expect_rejected(self, tmp_path):
        path = self._write(tmp_path, "    checks:\n      - call: \"Ф()\"\n")
        with pytest.raises(ValueError, match="expect"):
            load_tasks(path)

    def test_missing_call_rejected(self, tmp_path):
        path = self._write(tmp_path, "    checks:\n      - expect: \"5\"\n")
        with pytest.raises(ValueError, match="call"):
            load_tasks(path)

    def test_non_list_checks_rejected(self, tmp_path):
        path = self._write(tmp_path, "    checks: ой\n")
        with pytest.raises(ValueError, match="checks must be a list"):
            load_tasks(path)

    def test_tasks_without_checks_default_empty(self, tmp_path):
        tasks = load_tasks(self._write(tmp_path, ""))
        assert tasks[0].checks == ()

    def test_bundled_tasks_still_load(self):
        from harness_loop.evals import bundled_tasks_path

        tasks = load_tasks(bundled_tasks_path())
        assert len(tasks) >= 70  # v0.4 set


class TestTaskOutcomeLevels:
    def test_resolved_l1_none_without_exec(self):
        outcome = TaskOutcome(task=_task(), result=_result())
        assert outcome.resolved_l1 is None

    def test_resolved_l1_false_when_engine_missing(self):
        outcome = TaskOutcome(
            task=_task(),
            result=_result(),
            exec_outcome=ExecOutcome(ran=False, engine="x"),
        )
        assert outcome.resolved_l1 is None  # skip, not verdict

    def test_resolved_l1_from_outcome(self):
        ok = TaskOutcome(
            task=_task(),
            result=_result(),
            exec_outcome=ExecOutcome(ran=True, engine="x", passed=True),
        )
        bad = TaskOutcome(
            task=_task(),
            result=_result(),
            exec_outcome=ExecOutcome(ran=True, engine="x", passed=False),
        )
        assert ok.resolved_l1 is True and bad.resolved_l1 is False


class TestRunEvalWiring:
    def _loop(self):
        from unittest.mock import MagicMock

        loop = MagicMock()
        loop.run.return_value = _result()
        loop.judge = None
        return loop

    def test_executor_runs_only_for_tasks_with_checks(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1"})
        tasks = [
            _task(checks=[ExecCheck(call="Ф(1)", expect="1")]),
            _task(checks=()),  # no checks -> no exec call
        ]
        run_eval(tasks, loop, executor=executor)
        assert len(executor.calls) == 1

    def test_unavailable_executor_skips_l1(self):
        loop = self._loop()
        executor = FakeExecutor(unavailable=True)
        report = run_eval(
            [_task(checks=[ExecCheck(call="Ф(1)", expect="1")])], loop, executor=executor
        )
        assert report.l1_measured == 0
        assert report.l1_coverage == 1.0  # set property, engine-independent

    def test_report_levels(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1", "Ф(2)": "не-двойка"})
        tasks = [
            _task(checks=[ExecCheck(call="Ф(1)", expect="1")]),  # L1 pass
            _task(checks=[ExecCheck(call="Ф(2)", expect="2")]),  # L1 fail
            _task(checks=()),  # L0-only
        ]
        report = run_eval(tasks, loop, executor=executor)
        assert report.total == 3
        assert report.resolved_l0 == 3
        assert report.resolved_l1 == 1
        assert report.l1_measured == 2
        assert report.l1_coverage == pytest.approx(2 / 3)
        line = report.levels_line()
        assert "L0 3/3" in line and "L1 1/2" in line

    def test_levels_line_when_engine_absent(self):
        outcome = TaskOutcome(
            task=_task(checks=[ExecCheck(call="Ф(1)", expect="1")]), result=_result()
        )
        report = EvalReport(outcomes=[outcome])
        line = report.levels_line()
        assert "не измерялся" in line and "движок недоступен" in line

    def test_levels_line_when_no_checks_anywhere(self):
        report = EvalReport(outcomes=[TaskOutcome(task=_task(), result=_result())])
        assert "нет чеков" in report.levels_line()

    def test_to_dict_has_level_fields(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1"})
        report = run_eval(
            [_task(checks=[ExecCheck(call="Ф(1)", expect="1")])], loop, executor=executor
        )
        payload = report.to_dict()
        assert payload["resolved_l0"] == 1
        assert payload["resolved_l1"] == 1
        assert payload["l1_measured"] == 1
        assert payload["l1_coverage"] == 1.0
        assert payload["resolved"] == 1  # back-compat synonym
        task_payload = payload["tasks"][0]
        assert task_payload["resolved_l1"] is True
        assert task_payload["has_checks"] is True
        assert "exec" in task_payload

    def test_markdown_contains_levels(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1"})
        report = run_eval(
            [_task(checks=[ExecCheck(call="Ф(1)", expect="1")])], loop, executor=executor
        )
        md = report.to_markdown()
        assert "Уровни:" in md and "L0" in md and "L1" in md

    def test_case_fold_check_flows_through(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Т()": "выбрать  *  из  т"})
        report = run_eval(
            [_task(checks=[ExecCheck(call="Т()", expect="ВЫБРАТЬ * Из т", case_fold=True)])],
            loop,
            executor=executor,
        )
        assert report.resolved_l1 == 1

    def test_failed_generation_gives_failing_l1(self):
        loop = self._loop()
        loop.run.return_value = _result(passed=False, code="")
        executor = FakeExecutor(outputs={})
        report = run_eval(
            [_task(checks=[ExecCheck(call="Ф(1)", expect="1")])], loop, executor=executor
        )
        assert report.resolved_l1 == 0  # no code -> checks fail honestly
        assert report.l1_measured == 1


class TestEngineNote:
    """run_eval tells the generator which engine will execute the code."""

    def _loop(self):
        from unittest.mock import MagicMock

        loop = MagicMock()
        loop.run.return_value = _result()
        loop.judge = None
        return loop

    def test_ready_executor_prepends_note_for_tasks_with_checks(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1"})
        base = _task(checks=[ExecCheck(call="Ф(1)", expect="1")])
        task = replace(base, context="старый контекст")

        run_eval([task], loop, executor=executor)

        kwargs = loop.run.call_args_list[0].kwargs
        # the note rides in env_note — the context socket stays FREE for the
        # project provider (the E1/E-2 bug: note in context disabled it)
        assert "OneScript 2.2.0" in kwargs["env_note"]
        assert "ДобавитьКДате" in kwargs["env_note"]
        assert kwargs["context"] == "старый контекст"

    def test_ready_executor_note_does_not_fill_context_socket(self):
        """Regression (E-3): note must not block the project context provider.

        Before the fix run_eval stuffed ONESCRIPT_ENGINE_NOTE into `context`,
        so loop.run saw a non-empty socket and never called the provider —
        live evals with L1 ran without project context while CI (no real
        OneScript) stayed green because executor_ready was False there.
        """
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1"})
        task = _task(checks=[ExecCheck(call="Ф(1)", expect="1")])

        run_eval([task], loop, executor=executor)

        kwargs = loop.run.call_args_list[0].kwargs
        assert kwargs["context"] == ""  # socket free — provider may fill it
        assert "OneScript 2.2.0" in kwargs["env_note"]

    def test_task_without_checks_gets_no_note(self):
        loop = self._loop()
        executor = FakeExecutor(outputs={"Ф(1)": "1"})
        task = replace(_task(checks=()), context="чистый контекст")

        run_eval([task], loop, executor=executor)

        kwargs = loop.run.call_args_list[0].kwargs
        assert "OneScript" not in kwargs["env_note"]
        assert kwargs["env_note"] == ""
        assert kwargs["context"] == "чистый контекст"

    def test_unavailable_executor_gets_no_note(self):
        loop = self._loop()
        executor = FakeExecutor(unavailable=True)
        task = _task(checks=[ExecCheck(call="Ф(1)", expect="1")])

        run_eval([task], loop, executor=executor)

        kwargs = loop.run.call_args_list[0].kwargs
        assert "OneScript" not in kwargs["env_note"]
        assert kwargs["context"] == ""

    def test_no_executor_gets_no_note(self):
        loop = self._loop()
        task = _task(checks=[ExecCheck(call="Ф(1)", expect="1")])

        run_eval([task], loop)

        kwargs = loop.run.call_args_list[0].kwargs
        assert kwargs["env_note"] == ""
        assert kwargs["context"] == ""

    def test_note_mentiones_only_probe_confirmed_gaps(self):
        from harness_loop.prompt import ONESCRIPT_ENGINE_NOTE

        # every absent function named in the note was confirmed by a live
        # probe against vanilla oscript 2.2.0 (2026-10-05)
        for func in ("ДобавитьКДате", "ПериодСтр", "ПредставлениеПериода", "ЧислоПрописью"):
            assert func in ONESCRIPT_ENGINE_NOTE
        # and functions the probes confirmed PRESENT are not slandered
        absent_block = ONESCRIPT_ENGINE_NOTE.split(
            "НЕТ (Symbol not found):"
        )[1].split(";")[0]
        for present in ("НачалоМесяца", "Формат", "СтрШаблон"):
            assert present not in absent_block


class TestJudgeTelemetryInReport:
    """judge_samples/judge_agreement surface in the per-task report (E-2)."""

    def test_to_dict_carries_samples_and_agreement(self):
        iteration = IterationLog(
            index=1,
            context_source="builtin",
            context_tokens=1234,
            code_extracted=True,
            verified=True,
        )
        iteration.judge_verdict = False
        iteration.judge_samples = 3
        iteration.judge_agreement = 0.67
        result = LoopResult(
            passed=False, code="К", iterations=[iteration],
            failure_reason="judge_rejected",
        )
        outcome = TaskOutcome(task=_task(), result=result)
        payload = outcome.to_dict()
        assert payload["judge_samples"] == 3
        assert payload["judge_agreement"] == 0.67

    def test_to_dict_none_without_judge(self):
        result = _result()
        payload = TaskOutcome(task=_task(), result=result).to_dict()
        assert payload["judge_samples"] is None
        assert payload["judge_agreement"] is None

    def test_result_judge_wins_over_iterations(self):
        from harness_loop.judge import JudgeVerdict

        iteration = IterationLog(index=1, code_extracted=True, verified=True)
        iteration.judge_verdict = False
        iteration.judge_samples = 3
        result = LoopResult(
            passed=True,
            code="К",
            iterations=[iteration],
            judge=JudgeVerdict(approved=True, samples=1, agreement=1.0),
        )
        outcome = TaskOutcome(task=_task(), result=result)
        assert outcome.judge_samples == 1  # final verdict, not the veto loop
        assert outcome.judge_agreement == 1.0
