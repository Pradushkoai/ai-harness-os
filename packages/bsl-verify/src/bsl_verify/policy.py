"""Verification policy: when a check result counts as FAILED.

Default harness policy (L0/L1 backpressure): ANY error blocks the agent.
Warnings/info/hints are reported but do not fail — they feed the agent's
context, not the gate. Customizable per project via CLI flags or API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .types import FileReport, Severity


# Diagnostic codes that bsl-language-server reports as ERROR while the 1C
# platform (and OneScript) happily execute the code. Empirically confirmed
# on the UT 11 real-config pilot (2026-10-05):
#   InvalidCharacterInFile — raised on non-ASCII punctuation inside comments
#   (em-dash U+2014 etc.): a three-year-old config comment style that the
#   engine runs without a single complaint burned 3 loop iterations on a
#   non-problem. The harness keeps the code visible to the report but does
#   not let it fail the gate.
# The set is knowledge, not a default: import it where the loop policy is
# built (harness-loop merges it into --ignore unless --strict-verify).
LS_FALSE_POSITIVE_CODES: frozenset = frozenset({"InvalidCharacterInFile"})


@dataclass(frozen=True)
class VerifyPolicy:
    """Gate rules applied to a verification result.

    Attributes:
        max_errors: hard gate. Default 0 — any Error fails (parse errors,
            broken syntax — the agent must fix before proceeding).
        max_warnings: optional gate; None = warnings never fail the check.
        ignore_codes: diagnostic codes excluded from ALL counting.
        only_codes: if set, ONLY these codes are counted (whitelist mode).
    """

    max_errors: int = 0
    max_warnings: Optional[int] = None
    ignore_codes: frozenset = field(default_factory=frozenset)
    only_codes: Optional[frozenset] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ignore_codes", frozenset(self.ignore_codes))
        if self.only_codes is not None:
            object.__setattr__(self, "only_codes", frozenset(self.only_codes))

    # -- evaluation -----------------------------------------------------------

    def evaluate(self, reports: Iterable[FileReport]) -> "PolicyVerdict":
        errors = warnings = informations = hints = 0
        for report in reports:
            for diagnostic in report.diagnostics:
                if not self._counts(diagnostic.code):
                    continue
                if diagnostic.severity == Severity.ERROR:
                    errors += 1
                elif diagnostic.severity == Severity.WARNING:
                    warnings += 1
                elif diagnostic.severity == Severity.INFORMATION:
                    informations += 1
                elif diagnostic.severity == Severity.HINT:
                    hints += 1

        violations: list[str] = []
        if errors > self.max_errors:
            violations.append(
                f"errors: {errors} > allowed {self.max_errors}"
            )
        if self.max_warnings is not None and warnings > self.max_warnings:
            violations.append(
                f"warnings: {warnings} > allowed {self.max_warnings}"
            )
        return PolicyVerdict(
            passed=not violations,
            violations=violations,
            errors=errors,
            warnings=warnings,
            informations=informations,
            hints=hints,
        )

    def _counts(self, code: str) -> bool:
        if code and code in self.ignore_codes:
            return False
        if self.only_codes is not None and code not in self.only_codes:
            return False
        return True

    def describe(self) -> str:
        parts = [f"max_errors={self.max_errors}"]
        parts.append(
            f"max_warnings={'off' if self.max_warnings is None else self.max_warnings}"
        )
        if self.ignore_codes:
            parts.append(f"ignore={','.join(sorted(self.ignore_codes))}")
        if self.only_codes is not None:
            parts.append(f"only={','.join(sorted(self.only_codes))}")
        return f"VerifyPolicy({', '.join(parts)})"


@dataclass
class PolicyVerdict:
    passed: bool
    violations: list
    errors: int = 0
    warnings: int = 0
    informations: int = 0
    hints: int = 0
