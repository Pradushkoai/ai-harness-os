"""Core data types for bsl-verify.

Positional fields follow LSP: 0-based line/character. Human-facing output
(CLI summary) renders 1-based, like every editor does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional


class Severity(str, Enum):
    """Diagnostic severity, values as emitted by bsl-language-server JSON."""

    ERROR = "Error"
    WARNING = "Warning"
    INFORMATION = "Information"
    HINT = "Hint"


class BslVerifyError(Exception):
    """Base class for bsl-verify errors (environment / execution)."""


@dataclass(frozen=True)
class Position:
    line: int = 0
    character: int = 0


@dataclass(frozen=True)
class Range:
    start: Position = field(default_factory=Position)
    end: Position = field(default_factory=Position)


@dataclass(frozen=True)
class Diagnostic:
    range: Range = field(default_factory=Range)
    severity: Severity = Severity.INFORMATION
    code: str = ""
    message: str = ""
    tags: tuple = ()
    source: str = ""

    def format(self, file_label: str = "") -> str:
        """One-line, editor-style (1-based) rendering for humans and agents."""

        location = f"{file_label}:" if file_label else ""
        location += f"{self.range.start.line + 1}:{self.range.start.character + 1}"
        code = f" {self.code}" if self.code else ""
        return f"{location} {self.severity.value.upper()}{code}: {self.message}"


@dataclass
class FileMetrics:
    """Per-file code metrics from the analyze report. All optional."""

    procedures: Optional[int] = None
    functions: Optional[int] = None
    lines: Optional[int] = None
    ncloc: Optional[int] = None
    comments: Optional[int] = None
    statements: Optional[int] = None
    cognitive_complexity: Optional[int] = None
    cyclomatic_complexity: Optional[int] = None


@dataclass
class FileReport:
    path: str  # decoded filesystem path (file:// URI resolved)
    diagnostics: list = field(default_factory=list)
    metrics: Optional[FileMetrics] = None

    def count(self, severity: Severity) -> int:
        return sum(1 for d in self.diagnostics if d.severity == severity)


@dataclass
class VerifyResult:
    """Outcome of one verification run: reports + counts + policy verdict."""

    files: list = field(default_factory=list)  # list[FileReport]
    errors: int = 0
    warnings: int = 0
    informations: int = 0
    hints: int = 0
    passed: bool = True
    violations: list = field(default_factory=list)  # list[str]
    duration_ms: float = 0.0
    report_date: str = ""
    source_dir: str = ""
    policy: str = "VerifyPolicy()"

    # -- derived helpers -----------------------------------------------------

    @property
    def total_diagnostics(self) -> int:
        return self.errors + self.warnings + self.informations + self.hints

    def iter_diagnostics(self) -> Iterator[tuple[FileReport, Diagnostic]]:
        for report in self.files:
            for diagnostic in report.diagnostics:
                yield report, diagnostic

    def summary_line(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        counts = (
            f"{self.total_diagnostics} diagnostic(s): "
            f"{self.errors} error, {self.warnings} warning, "
            f"{self.informations} info, {self.hints} hint"
        )
        return (
            f"BSL check: {len(self.files)} file(s), {counts} "
            f"[{status}; policy {self.policy}; {self.duration_ms / 1000.0:.1f}s]"
        )

    def summary_lines(self) -> list[str]:
        """Full human/agent-readable report: summary + one line per issue."""

        lines = [self.summary_line()]
        if self.violations:
            lines.extend(f"  ! {v}" for v in self.violations)
        for report, diagnostic in self.iter_diagnostics():
            label = _short_label(report.path)
            lines.append(f"  {diagnostic.format(label)}")
        return lines

    def to_dict(self) -> dict:
        """JSON-safe representation (used by `bsl-check --json`)."""

        return {
            "passed": self.passed,
            "policy": self.policy,
            "violations": list(self.violations),
            "counts": {
                "files": len(self.files),
                "diagnostics": self.total_diagnostics,
                "errors": self.errors,
                "warnings": self.warnings,
                "informations": self.informations,
                "hints": self.hints,
            },
            "duration_ms": round(self.duration_ms, 1),
            "report_date": self.report_date,
            "source_dir": self.source_dir,
            "files": [
                {
                    "path": report.path,
                    "metrics": (
                        {
                            k: v
                            for k, v in vars(report.metrics).items()
                            if v is not None
                        }
                        if report.metrics
                        else None
                    ),
                    "diagnostics": [
                        {
                            "line": d.range.start.line + 1,
                            "column": d.range.start.character + 1,
                            "severity": d.severity.value,
                            "code": d.code,
                            "message": d.message,
                            "tags": list(d.tags),
                        }
                        for d in report.diagnostics
                    ],
                }
                for report in self.files
            ],
        }


def _short_label(path: str, limit: int = 40) -> str:
    """Shorten an absolute path for compact diagnostic lines."""

    if len(path) <= limit:
        return path
    return "..." + path[-(limit - 3):]
