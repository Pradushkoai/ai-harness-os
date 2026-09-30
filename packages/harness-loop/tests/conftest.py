"""Shared fakes: scripted LLM port + scripted verifier (no world, no tokens, no java).

FakeLLMPort — scripts the texts the "model" returns, records every call's
messages so tests can assert what the loop actually fed back to the model.
FakeVerifier — scripts VerifyResults; records the code texts it was asked
to verify. Both satisfy the interfaces the loop depends on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from bsl_verify.types import Diagnostic, FileReport, Position, Range, Severity, VerifyResult
from russian_llm_pack.types import ChatMessage, CompletionResult, UsageInfo

# Known against the real bsl-language-server v1.0.7 (see bsl-verify fixtures):
#   module_ok    — valid procedure, only INFORMATION diagnostics -> passes policy
#   module_broken — `Если ... Тогда` without КонецЕсли -> ParseError (Error)
MODULE_OK = (
    "Процедура ТестоваяПроцедура()\n"
    "    ЛокальнаяПеременная = 1;\n"
    "    Сообщить(ЛокальнаяПеременная);\n"
    "КонецПроцедуры\n"
)

MODULE_BROKEN = (
    "Процедура СломаннаяПроцедура()\n"
    "    Если 1 Тогда\n"
    "\n"
    "КонецПроцедуры\n"
)


class FakeLLMPort:
    """LLMPort-shaped fake with scripted responses."""

    name = "fake"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []
        self.kwargs: list[dict] = []

    def complete(self, messages, *, model=None, **kwargs):
        self.calls.append(list(messages))
        self.kwargs.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("FakeLLMPort: no scripted response left")
        text = self.responses.pop(0)
        return CompletionResult(
            text=text,
            provider="fake",
            model=model or "fake-model",
            usage=UsageInfo(input_tokens=100, output_tokens=50),
            latency_ms=12.5,
        )

    def stream(self, messages, *, model=None, **kwargs):  # pragma: no cover
        raise NotImplementedError


class FakeVerifier:
    """BslVerifier-shaped fake: pops one scripted result per verify call."""

    def __init__(self, results: list[VerifyResult]):
        self.results = list(results)
        self.seen_texts: list[str] = []
        self.seen_filenames: list[str] = []

    def verify_module_text(self, text: str, *, filename: str = "module.bsl"):
        self.seen_texts.append(text)
        self.seen_filenames.append(filename)
        if not self.results:
            raise AssertionError("FakeVerifier: no scripted result left")
        return self.results.pop(0)


def make_verify_result(
    passed: bool,
    diagnostics: list[tuple[str, Severity, int]] | None = None,
    duration_ms: float = 100.0,
) -> VerifyResult:
    """Build a VerifyResult with diagnostics as (code, severity, line) tuples."""

    diags = [
        Diagnostic(
            range=Range(start=Position(line=line, character=0)),
            severity=severity,
            code=code,
            message=f"диагностика {code}",
        )
        for code, severity, line in (diagnostics or [])
    ]
    errors = sum(1 for d in diags if d.severity == Severity.ERROR)
    warnings = sum(1 for d in diags if d.severity == Severity.WARNING)
    infos = sum(1 for d in diags if d.severity == Severity.INFORMATION)
    return VerifyResult(
        files=[FileReport(path="module.bsl", diagnostics=diags)],
        errors=errors,
        warnings=warnings,
        informations=infos,
        passed=passed,
        violations=[] if passed else [f"errors: {errors} > allowed 0"],
        duration_ms=duration_ms,
        policy="VerifyPolicy(max_errors=0, max_warnings=off)",
    )


def fenced(code: str, lang: str = "bsl") -> str:
    """Wrap code into a markdown fence, as the model is asked to reply."""

    return f"Вот модуль:\n```{lang}\n{code.rstrip()}\n```\nГотово."


@pytest.fixture
def ok_result() -> VerifyResult:
    return make_verify_result(passed=True)


@pytest.fixture
def broken_result() -> VerifyResult:
    return make_verify_result(
        passed=False,
        diagnostics=[("ParseError", Severity.ERROR, 4)],
    )


__all__ = [
    "MODULE_BROKEN",
    "MODULE_OK",
    "FakeLLMPort",
    "FakeVerifier",
    "fenced",
    "make_verify_result",
]
