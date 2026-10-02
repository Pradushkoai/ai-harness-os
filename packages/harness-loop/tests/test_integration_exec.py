"""INTEGRATION tests — the L1 execution oracle on the real OneScript engine.

A4-lite gate (roadmap 2.1): every benchmark task that carries checks must
have a REFERENCE solution passing its own checks on the real engine —
broken expectations never land in the set. This is the live counterpart
of scripts/gen_checks.py's round-trip stage, kept in the suite so the
gate runs on every machine that has oscript installed.

Run explicitly:
    OSCRIPT_PATH=/path/to/oscript pytest -m integration -v
"""

from __future__ import annotations

import os
import shutil

import pytest

from harness_loop.evals import bundled_tasks_path, load_tasks
from harness_loop.executors import ExecCheck, OneScriptRunner

pytestmark = pytest.mark.integration


def engine_available() -> bool:
    return (
        bool(os.environ.get("OSCRIPT_PATH"))
        or shutil.which("oscript") is not None
        or bool(os.environ.get("OSCRIPT_HOME"))
    )


@pytest.fixture(scope="module")
def runner() -> OneScriptRunner:
    discovered = OneScriptRunner.discover()
    if discovered is None:
        pytest.skip("OneScript engine not available (set OSCRIPT_PATH)")
    return discovered


def test_engine_runs_a_module(runner):
    outcome = runner.run_checks(
        "Функция Пять()\n    Возврат 5;\nКонецФункции",
        [ExecCheck(call="Пять()", expect="5")],
    )
    assert outcome.ran
    assert outcome.passed
    assert outcome.results[0].actual == "5"


def test_all_tasks_with_checks_pass_own_reference(runner):
    """The A4 gate: no task ships checks its own reference cannot pass."""

    tasks = [t for t in load_tasks(bundled_tasks_path()) if t.checks]
    assert len(tasks) >= 40, f"expected 40+ executable tasks, got {len(tasks)}"
    failures = []
    for task in tasks:
        outcome = runner.run_checks(task.reference, list(task.checks))
        if not outcome.passed:
            bad = [r.call for r in outcome.results if not r.passed]
            failures.append(f"{task.id}: {bad}")
    assert not failures, "references failing their own checks:\n" + "\n".join(failures)


def test_executed_code_can_fail_checks(runner):
    """A module that compiles and verifies can still be WRONG at L1 —
    the whole point of the oracle (verifier says clean, output says no)."""

    from harness_loop.executors import ExecCheck

    wrong = "Функция СуммаДвухЧисел(А, Б)\n    Возврат А - Б;\nКонецФункции"
    outcome = runner.run_checks(wrong, [ExecCheck(call="СуммаДвухЧисел(2, 3)", expect="5")])
    assert outcome.ran
    assert outcome.passed is False
    assert outcome.results[0].actual == "-1"


def test_runtime_error_is_captured_not_fatal(runner):
    outcome = runner.run_checks(
        "Функция Бум()\n    Возврат 1 / 0;\nКонецФункции",
        [ExecCheck(call="Бум()", expect="1")],
    )
    assert outcome.passed is False
    assert outcome.results[0].error  # divide-by-zero surfaced as check failure
