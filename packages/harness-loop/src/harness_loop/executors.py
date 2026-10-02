"""L1 execution oracle: run BSL modules in OneScript, compare output.

The verifier (bsl-verify) answers "is the module syntactically clean?"
(L0). This module answers the next question — "does it *work*?" (L1):
the module is wrapped into a generated driver, executed by the OneScript
engine in an isolated temp directory, and the printed values of each
check expression are compared with the expected ones.

Port shape (mirrors BslVerify's runner pattern):
    OneScriptRunner.discover()  -> executor or None (env OSCRIPT_PATH ->
    PATH -> OSCRIPT_HOME/bin);  executor.available() is the live check.
    run_checks(module, checks)  -> ExecOutcome with per-check results.

Isolation rules (roadmap 2.1, step A1):
    - execution happens in a fresh temp directory (cwd is empty);
    - the child environment is scrubbed of secret-looking variables
      (API keys, tokens, passwords) — the oracle must never leak
      provider credentials into a generated module's process;
    - hard wall-clock timeout per run (default 15s) — a runaway module
      (infinite loop) is a FAILED check, not a hung harness.

Driver protocol (per check N):
    Попытка
        РезультатВызова = <call>;
        Сообщить("__CHKN__");
        Сообщить(РезультатВызова);
    Исключение
        Сообщить("__CHKN__");
        Сообщить("__ERROR__: " + ОписаниеОшибки());
    КонецПопытки;

The marker line makes the parser robust to noisy modules: a generated
module may print anything, but each check's value is "the lines between
its marker and the next marker". A runtime error in the check expression
is reported as __ERROR__ and fails that check — the run continues.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

# Anything that smells like a credential never reaches the child process.
_SECRET_ENV = re.compile(
    r"(API[_-]?KEY|TOKEN|SECRET|PASSW|CREDENTIAL|CERT|PRIVATE)", re.IGNORECASE
)
_MARKER = re.compile(r"^__CHK(\d+)__$", re.ASCII)
_DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class ExecCheck:
    """One oracle probe: evaluate `call`, expect the printed value.

    Attributes:
        call: a BSL *expression* (typically a function call with concrete
            arguments) evaluated against the module under test. For
            procedure tasks (proc=True) it is the procedure invocation
            statement, and `expect` is whatever the procedure prints.
        expect: the expected Сообщить() output, compared after
            normalization (trailing whitespace / blank trailing lines /
            CRLF are ignored).
        setup: optional BSL statements (separated by ;) executed right
            before the call inside the same try block — used to build
            non-trivial inputs (arrays, tables, structures) that are
            painful to inline into one expression. Variables persist
            across checks of one module run.
        case_fold: loose comparison for text-builder tasks (query texts):
            keyword case and whitespace runs do not change the verdict.
        proc: the call is a procedure (no return value) — the expected
            output is what the procedure itself prints.
    """

    call: str
    expect: str = ""
    setup: str = ""
    case_fold: bool = False
    proc: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.call, str) or not self.call.strip():
            raise ValueError(
                f"ExecCheck.call must be a non-empty BSL expression, got {self.call!r}"
            )
        if not isinstance(self.expect, str):
            raise ValueError(f"ExecCheck.expect must be a string, got {self.expect!r}")
        if not isinstance(self.setup, str):
            raise ValueError(f"ExecCheck.setup must be a string, got {self.setup!r}")


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single check: expected vs actually printed."""

    call: str
    expect: str
    actual: str
    passed: bool
    error: str = ""  # runtime error inside the check expression, if any


@dataclass
class ExecOutcome:
    """Result of one module execution (all checks in a single engine run).

    ran=False means the engine itself was unavailable (no oscript on the
    machine) — a *skip*, not a verdict: L1 simply was not measured.
    """

    ran: bool
    engine: str = ""
    passed: bool = False
    results: list = field(default_factory=list)  # list[CheckResult]
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ran": self.ran,
            "engine": self.engine,
            "passed": self.passed,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
            "checks": [
                {
                    "call": r.call,
                    "expect": r.expect,
                    "actual": r.actual,
                    "passed": r.passed,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


def normalize_output(text: str, case_fold: bool = False) -> str:
    """Canonical form for comparing printed values.

    Always: CRLF/CR -> LF, per-line trailing whitespace dropped, blank
    trailing lines dropped. With case_fold (query-text mode): lowercase
    and collapse internal whitespace runs to a single space — so
    "ВЫБРАТЬ  * Из  Справочник.Номенклатура" == "выбрать * из справочник.номенклатура".
    """

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if case_fold:
        lines = [re.sub(r"[ \t]+", " ", ln).strip().lower() for ln in lines]
    else:
        lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def sanitize_env(env: Optional[dict] = None) -> dict:
    """Child-process environment without secret-looking variables."""

    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not _SECRET_ENV.search(k)}


def build_driver(module_code: str, checks: list[ExecCheck]) -> str:
    """Wrap a module + checks into one executable OneScript script."""

    lines = [module_code.rstrip(), ""]
    lines.append("// __HARNESS_DRIVER__ — сгенерировано исполнителем L1, не редактировать")
    lines.append("")
    for index, check in enumerate(checks, start=1):
        marker = f"__CHK{index}__"
        setup = [
            "    " + stmt.strip().rstrip(";") + ";"
            for stmt in check.setup.split(";")
            if stmt.strip()
        ]
        lines.append(f"Попытка // {marker}")
        lines.extend(setup)
        if check.proc:
            lines.append(f"    Сообщить(\"{marker}\");")
            lines.append(f"    {check.call.strip()};")
        else:
            lines.append(f"    РезультатВызова = {check.call.strip()};")
            lines.append(f"    Сообщить(\"{marker}\");")
            lines.append("    Сообщить(РезультатВызова);")
        lines.append("Исключение")
        lines.append(f"    Сообщить(\"{marker}\");")
        lines.append("    Сообщить(\"__ERROR__: \" + ОписаниеОшибки());")
        lines.append("КонецПопытки;")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_driver_output(stdout: str, check_count: int) -> list[Optional[str]]:
    """Split driver stdout into per-check printed values.

    Returns a list of length check_count; entries are the raw multi-line
    value printed after each marker, or None when the marker never
    appeared (module crashed / printed nothing for that check).
    """

    values: list[Optional[str]] = [None] * check_count
    current: Optional[int] = None
    buffer: list[str] = []

    def _flush() -> None:
        nonlocal buffer
        # Сообщить() appends "\n" after the value, which split() turns into
        # one trailing empty element — drop exactly that one, keep the rest.
        if buffer and buffer[-1] == "":
            buffer = buffer[:-1]
        if current is not None:
            values[current - 1] = "\n".join(buffer)
        buffer = []

    for line in stdout.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _MARKER.match(line.strip())
        if match:
            _flush()
            current = int(match.group(1))
        elif current is not None:
            buffer.append(line)
    _flush()
    return values


@runtime_checkable
class ExecutorPort(Protocol):
    """The L1 seam: everything the eval needs from an execution engine."""

    name: str

    def available(self) -> bool: ...

    def run_checks(
        self, module_code: str, checks: list[ExecCheck], timeout: float = _DEFAULT_TIMEOUT
    ) -> ExecOutcome: ...


class OneScriptRunner:
    """ExecutorPort adapter over the OneScript (oscript) CLI engine.

    Discovery order: OSCRIPT_PATH (full binary path) -> PATH ->
    OSCRIPT_HOME/bin/oscript(.exe). unavailable() environments get
    ran=False outcomes — the eval reports L1 as not measured, mirroring
    the bsl-verify java/jar skip behaviour.
    """

    name = "onescript"

    def __init__(self, binary: str, timeout: float = _DEFAULT_TIMEOUT):
        self._binary = binary
        self._timeout = timeout

    @classmethod
    def discover(cls, timeout: float = _DEFAULT_TIMEOUT) -> Optional["OneScriptRunner"]:
        explicit = os.environ.get("OSCRIPT_PATH", "")
        if explicit:
            candidate = Path(explicit)
            suffixes = candidate.suffixes or []
            if candidate.is_file() or any(s.lower() == ".exe" for s in suffixes):
                return cls(str(candidate), timeout)
            return None  # explicit but missing — do not silently fall back
        for exe in ("oscript", "oscript.exe"):
            found = shutil.which(exe)
            if found:
                return cls(found, timeout)
        home = os.environ.get("OSCRIPT_HOME", "")
        if home:
            for exe in ("oscript", "oscript.exe"):
                candidate = Path(home) / "bin" / exe
                if candidate.is_file():
                    return cls(str(candidate), timeout)
        return None

    def available(self) -> bool:
        return Path(self._binary).exists() or shutil.which(self._binary) is not None

    def _outcome_from_stdout(
        self, stdout: str, checks: list[ExecCheck], duration_ms: float
    ) -> ExecOutcome:
        raw_values = parse_driver_output(stdout, len(checks))
        results: list[CheckResult] = []
        for check, raw in zip(checks, raw_values):
            if raw is None:
                results.append(
                    CheckResult(
                        call=check.call,
                        expect=check.expect,
                        actual="",
                        passed=False,
                        error="нет вывода: маркер проверки не найден (модуль упал до вызова)",
                    )
                )
                continue
            error = ""
            error_prefix = "__ERROR__: "
            if raw.startswith(error_prefix):
                error = raw[len(error_prefix):].strip()
                results.append(
                    CheckResult(
                        call=check.call,
                        expect=check.expect,
                        actual="",
                        passed=False,
                        error=error,
                    )
                )
                continue
            actual = normalize_output(raw, case_fold=check.case_fold)
            expected = normalize_output(check.expect, case_fold=check.case_fold)
            results.append(
                CheckResult(
                    call=check.call,
                    expect=check.expect,
                    actual=actual,
                    passed=actual == expected,
                )
            )
        return ExecOutcome(
            ran=True,
            engine=self.name,
            passed=all(r.passed for r in results),
            results=results,
            duration_ms=duration_ms,
        )

    def run_checks(
        self, module_code: str, checks: list[ExecCheck], timeout: float = _DEFAULT_TIMEOUT
    ) -> ExecOutcome:
        if not checks:
            return ExecOutcome(ran=True, engine=self.name, passed=True, results=[])
        script = build_driver(module_code, checks)
        workdir = tempfile.mkdtemp(prefix="harness-l1-")
        script_path = Path(workdir) / "driver.os"
        script_path.write_text(script, encoding="utf-8")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [self._binary, script_path.name],
                cwd=workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout if timeout <= 0 else timeout,
                env=sanitize_env(),
            )
        except subprocess.TimeoutExpired:
            return ExecOutcome(
                ran=True,
                engine=self.name,
                passed=False,
                error=f"таймаут исполнения: {self._timeout:.0f}с (вероятен бесконечный цикл)",
                duration_ms=time.perf_counter() - started,
            )
        except OSError as exc:  # engine binary vanished mid-run
            return ExecOutcome(
                ran=True,
                engine=self.name,
                passed=False,
                error=f"ошибка запуска движка: {exc}",
                duration_ms=time.perf_counter() - started,
            )
        duration_ms = (time.perf_counter() - started) * 1000.0
        stdout = completed.stdout or ""
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            outcome = self._outcome_from_stdout(stdout, checks, duration_ms)
            outcome.error = (
                f"движок завершился с кодом {completed.returncode}"
                + (f": {stderr[:500]}" if stderr else "")
            )
            outcome.passed = False
            return outcome
        return self._outcome_from_stdout(stdout, checks, duration_ms)
