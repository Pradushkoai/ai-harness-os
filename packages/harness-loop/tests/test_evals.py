"""Mini SWE-bench-BSL tests: task loading, runner, reports (fakes only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_loop.evals import (
    BslTask,
    EvalReport,
    TaskOutcome,
    bundled_tasks_path,
    load_tasks,
    run_eval,
)
from harness_loop.judge import Judge
from harness_loop.loop import BslAgentLoop
from harness_loop.types import LoopConfig

from conftest import MODULE_OK, FakeLLMPort, FakeVerifier, fenced, make_verify_result

TASKS_YAML = """\
version: 1
tasks:
  - id: task-a
    category: function
    difficulty: easy
    prompt: "Задача А"
    reference: |
      Функция А()
          Возврат 1;
      КонецФункции
  - id: task-b
    difficulty: hard
    prompt: "Задача Б"
    context: "конфигурация УТ"
    reference: |
      Функция Б()
          Возврат 2;
      КонецФункции
"""


class TestLoadTasks:
    def test_bundled_set_loads(self):
        tasks = load_tasks(bundled_tasks_path())
        assert len(tasks) == 10
        ids = [t.id for t in tasks]
        assert len(set(ids)) == 10  # all unique
        assert "func-sum-two-numbers" in ids
        assert all(t.prompt for t in tasks)
        assert all(t.reference.strip() for t in tasks)

    def test_bundled_difficulties_and_categories(self):
        tasks = {t.id: t for t in load_tasks(bundled_tasks_path())}
        assert tasks["func-sum-two-numbers"].difficulty == "easy"
        assert tasks["proc-safe-division"].category == "errors"
        assert tasks["func-array-sum"].difficulty == "medium"

    def test_yaml_roundtrip_fields(self, tmp_path):
        source = tmp_path / "tasks.yaml"
        source.write_text(TASKS_YAML, encoding="utf-8")
        tasks = load_tasks(source)

        assert len(tasks) == 2
        first, second = tasks
        assert first.id == "task-a"
        assert first.category == "function"
        assert first.difficulty == "easy"
        assert "Возврат 1" in first.reference
        assert second.context == "конфигурация УТ"
        assert second.difficulty == "hard"
        assert second.category == "general"  # default

    def test_duplicate_ids_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "version: 1\ntasks:\n  - id: x\n    prompt: a\n"
            "  - id: x\n    prompt: b\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate task id: x"):
            load_tasks(bad)

    def test_missing_prompt_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 1\ntasks:\n  - id: x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="id/prompt"):
            load_tasks(bad)

    def test_unsupported_version_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 2\ntasks: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="version"):
            load_tasks(bad)

    def test_empty_tasks_rejected(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("version: 1\ntasks: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="no tasks"):
            load_tasks(empty)

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            load_tasks(tmp_path / "nope.yaml")


class TestRunEval:
    def test_all_resolved(self):
        tasks = load_tasks(_write_tasks())
        llm = FakeLLMPort([fenced(MODULE_OK)] * 2)
        verifier = FakeVerifier([make_verify_result(passed=True)] * 2)
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=2))
        report = run_eval(tasks, loop)

        assert report.total == 2
        assert report.resolved == 2
        assert report.pass_rate == 1.0
        assert report.failure_reasons() == {}

    def test_partial_resolution(self, tmp_path):
        tasks = load_tasks(_write_tasks())
        broken = make_verify_result(passed=False, diagnostics=None)
        llm = FakeLLMPort([fenced(MODULE_OK)] * 4)  # 2 iterations per task
        verifier = FakeVerifier([broken, broken, make_verify_result(True), broken])
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=2))
        report = run_eval(tasks, loop)

        assert report.resolved == 1
        assert report.pass_rate == 0.5
        assert report.failure_reasons() == {"budget_exhausted": 1}

    def test_context_reaches_llm(self, tmp_path):
        tasks = load_tasks(_write_tasks())
        llm = FakeLLMPort([fenced(MODULE_OK)] * 2)
        verifier = FakeVerifier([make_verify_result(True)] * 2)
        loop = BslAgentLoop(llm, verifier, LoopConfig())
        run_eval(tasks, loop)

        second_prompt = llm.calls[1][1].content
        assert "конфигурация УТ" in second_prompt

    def test_on_task_progress_callback(self, tmp_path):
        tasks = load_tasks(_write_tasks())
        llm = FakeLLMPort([fenced(MODULE_OK)] * 2)
        verifier = FakeVerifier([make_verify_result(True)] * 2)
        loop = BslAgentLoop(llm, verifier, LoopConfig())

        seen: list[str] = []
        run_eval(tasks, loop, on_task=lambda o: seen.append(o.task.id))
        assert seen == ["task-a", "task-b"]

    def test_judge_veto_fails_the_task(self, tmp_path):
        tasks = load_tasks(_write_tasks())
        llm = FakeLLMPort([fenced(MODULE_OK)] * 2)
        verifier = FakeVerifier([make_verify_result(True)] * 2)
        judge = Judge(FakeLLMPort(["VERDICT: FAIL\nISSUES:\n- плохо\n"] * 2))
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=1), judge=judge)
        report = run_eval(tasks, loop)

        assert report.resolved == 0
        assert report.failure_reasons() == {"judge_rejected": 2}

    def test_broken_callback_never_stops_eval(self, tmp_path):
        tasks = load_tasks(_write_tasks())
        llm = FakeLLMPort([fenced(MODULE_OK)] * 2)
        verifier = FakeVerifier([make_verify_result(True)] * 2)
        loop = BslAgentLoop(llm, verifier, LoopConfig())

        def boom(outcome):
            raise RuntimeError("progress callback exploded")

        report = run_eval(tasks, loop, on_task=boom)
        assert report.resolved == 2


class TestReport:
    def _report(self) -> EvalReport:
        tasks = load_tasks(_write_tasks())
        llm = FakeLLMPort([fenced(MODULE_OK), fenced(MODULE_OK)])
        broken = make_verify_result(passed=False, diagnostics=None)
        verifier = FakeVerifier([make_verify_result(True), broken])
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=1))
        return run_eval(tasks, loop)

    def test_summary_lines(self):
        lines = "\n".join(self._report().summary_lines())
        assert "BSL eval: 1/2 resolved (50%)" in lines
        assert "[PASS] task-a" in lines
        assert "[FAIL] task-b" in lines
        assert "budget_exhausted" in lines

    def test_to_dict_shape(self):
        payload = self._report().to_dict()
        assert payload["total"] == 2
        assert payload["resolved"] == 1
        assert payload["failure_reasons"] == {"budget_exhausted": 1}
        assert payload["tasks"][0]["id"] == "task-a"
        assert payload["tasks"][0]["resolved"] is True
        assert "reference" in payload["tasks"][0]
        assert payload["tasks"][0]["prompt_tokens"] > 0

    def test_markdown_report(self):
        text = self._report().to_markdown()
        assert "решено: **1**" in text
        assert "| ✅ | task-a |" in text
        assert "| ❌ | task-b |" in text
        assert "budget_exhausted" in text

    def test_save_json_and_markdown(self, tmp_path):
        report = self._report()
        json_path = report.save_json(tmp_path / "report.json")
        md_path = report.save_markdown(tmp_path / "report.md")

        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert loaded["total"] == 2
        assert "Mini SWE-bench-BSL" in md_path.read_text(encoding="utf-8")

    def test_empty_report_properties(self):
        report = EvalReport(outcomes=[])
        assert report.pass_rate == 0.0
        assert report.total_iterations == 0


def _write_tasks(tmp: Path | None = None) -> Path:
    """Write the two-task set; shared by runner tests via a module cache."""

    global _TASKS_PATH
    try:
        return _TASKS_PATH
    except NameError:
        import tempfile

        _TASKS_PATH = Path(tempfile.mkdtemp()) / "tasks.yaml"
        _TASKS_PATH.write_text(TASKS_YAML, encoding="utf-8")
        return _TASKS_PATH
