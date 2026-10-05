"""Shared fakes: the whole tool surface runs without network/java/keys.

Mirrors harness-loop's port-test philosophy — services get injectable
factories, tests feed canned LoopResult / VerifyResult objects.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from bsl_verify.types import (
    Diagnostic,
    FileReport,
    Position,
    Range,
    Severity,
    VerifyResult,
)
from harness_loop.types import IterationLog, LoopResult

from harness_mcp.state import SessionState
from harness_mcp.tools import HarnessServices


class FakeLoop:
    """BslAgentLoop stand-in: returns a canned LoopResult per task."""

    judge = None  # run_eval reads loop.judge to compute judge_mode

    def __init__(self, results=None):
        self.calls: list[tuple[str, str]] = []
        self._results = results or {}

    def run(self, task, context="", reference="", on_iteration=None, env_note=""):
        self.calls.append((task, context))
        return self._results.get(task, _ok_result(task))


def _ok_result(task: str) -> LoopResult:
    iteration = IterationLog(index=1, model="fake-model", code_extracted=True, verified=True)
    code = f"// {task}\nФункция Решение()\nКонецФункции"
    return LoopResult(passed=True, code=code, iterations=[iteration])


class FakeVerifier:
    """BslVerifier stand-in: verify_module_text -> canned VerifyResult."""

    def __init__(self, result: VerifyResult = None, raises: Exception = None):
        self._result = result
        self._raises = raises
        self.calls: list[str] = []

    def verify_module_text(self, text, *, filename="module.bsl"):
        self.calls.append(text)
        if self._raises is not None:
            raise self._raises
        return self._result or _clean_result()


def _clean_result() -> VerifyResult:
    report = FileReport(path="module.bsl", diagnostics=[])
    return VerifyResult(files=[report], passed=True)


def _broken_result() -> VerifyResult:
    diagnostic = Diagnostic(
        code="ParseError",
        severity=Severity.ERROR,
        message="не хватает КонецФункции",
        range=Range(start=Position(line=2, character=0), end=Position(line=2, character=0)),
    )
    report = FileReport(path="module.bsl", diagnostics=[diagnostic])
    return VerifyResult(files=[report], errors=1, passed=False)


@pytest.fixture
def fake_verifier():
    return FakeVerifier(result=_broken_result())


@pytest.fixture
def fake_loop():
    return FakeLoop()


@pytest.fixture
def services(fake_loop, fake_verifier):
    """Services wired to fakes — no network, no java, no keys."""

    return HarnessServices(
        loop_factory=lambda project_path="": fake_loop,
        verifier_factory=lambda: fake_verifier,
        state=SessionState(now=lambda: 1000.0),
    )
