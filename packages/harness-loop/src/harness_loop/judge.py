"""LLM-as-judge: a second opinion about the generated BSL module.

The judge is an advisory quality gate (L2) ON TOP of the verifier gate
(L0/L1): it reviews (a) the original task, (b) the generated module and
(c) residual verifier diagnostics, then answers in a strict format:

    VERDICT: PASS|FAIL
    SCORE: 0-10
    ISSUES:
    - <problem>
    REASONING: <one-two sentences>

Reference-aware mode (v0.5): `review(..., reference=...)` additionally
shows the task's gold solution and instructs the judge to compare the
SEMANTICS (formulas, boundaries, edge cases) instead of guessing from
the prompt alone. The reference is judge-only input: leaking it into
generator prompts would invalidate the benchmark (the model would just
copy the etalon).

Design rules (documented, tested):
    - the judge NEVER sees secrets and never calls the verifier;
    - the parser is tolerant: RU section names and verdict words are
      accepted; an explicit FAIL always vetoes, everything ambiguous
      counts as PASS (the verifier stays the authority — the judge may
      only veto explicitly, never approve code the verifier rejected);
    - a judge infrastructure failure (RLLError) does NOT fail the loop —
      the caller records it and keeps the verifier verdict.

Routing: the judge is constructed over any LLMPort-shaped object; in
practice a RouterPort with its own chain (`--judge-chain judge`), so the
second opinion can come from a different model than the generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from russian_llm_pack import ChatMessage, CompletionResult

from .prompt import (
    JUDGE_REFERENCE_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    judge_review_prompt,
)

# -- verdict classification --------------------------------------------------------

_PASS_WORDS = ("PASS", "APPROV", "OK", "ПРОЙД", "ПРИНЯТ")
_FAIL_WORDS = ("FAIL", "REJECT", "ОТКЛОН", "НЕ ПРОЙД", "НЕ ПРОШЕЛ", "НЕ ПРОШЁЛ", "БРАК")

_VERDICT_RE = re.compile(r"^\s*(?:verdict|вердикт)\s*[:=]\s*(.+?)\s*$", re.I | re.M)
_SCORE_RE = re.compile(r"^\s*(?:score|оценка|балл)\s*[:=]\s*([0-9]{1,2})", re.I | re.M)
_ISSUES_RE = re.compile(r"^\s*(?:issues|проблемы|замечания)\s*[:=]\s*$", re.I)
_REASONING_RE = re.compile(r"^\s*(?:reasoning|обоснование|комментарий)\s*[:=]\s*(.*)$", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")


def _classify(value: str) -> tuple[bool, bool]:
    """(approved, parsed) for one verdict value; FAIL words win."""

    if any(word in value for word in _FAIL_WORDS):
        return False, True
    if any(word in value for word in _PASS_WORDS):
        return True, True
    return True, False  # ambiguous -> no veto (verifier stays the authority)


@dataclass(frozen=True)
class JudgeConfig:
    """Judge behaviour knobs (temperature etc. are judge-specific)."""

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_issues: int = 10

    def __post_init__(self) -> None:
        if self.max_issues < 1:
            raise ValueError(f"max_issues must be >= 1, got {self.max_issues}")


@dataclass(frozen=True)
class JudgeVerdict:
    """Parsed judge answer (raw text kept for debugging/telemetry)."""

    approved: bool
    score: Optional[int] = None
    issues: tuple = ()          # tuple[str]
    reasoning: str = ""
    parsed: bool = True         # False -> model ignored the format, no veto applied
    model: str = ""
    latency_ms: float = 0.0
    raw: str = ""


def parse_judge_response(text: str, *, max_issues: int = 10) -> JudgeVerdict:
    """Tolerantly parse a judge answer.

    Explicit FAIL vetoes; an answer without a recognizable verdict counts
    as PASS with parsed=False (the judge may only veto explicitly).
    """

    match = _VERDICT_RE.search(text)
    if match:
        approved, parsed = _classify(match.group(1).upper())
    elif any(word in text.upper() for word in _FAIL_WORDS):
        approved, parsed = False, False  # fail said in prose, not in format
    else:
        approved, parsed = True, False

    score: Optional[int] = None
    score_match = _SCORE_RE.search(text)
    if score_match:
        score = max(0, min(10, int(score_match.group(1))))

    issues: list[str] = []
    reasoning = ""
    lines = text.splitlines()
    in_issues = False
    reasoning_parts: list[str] = []
    for line in lines:
        if _ISSUES_RE.match(line):
            in_issues = True
            continue
        reasoning_match = _REASONING_RE.match(line)
        if reasoning_match:
            in_issues = False
            reasoning_parts.append(reasoning_match.group(1).strip())
            continue
        if _VERDICT_RE.match(line) or _SCORE_RE.match(line):
            in_issues = False
            continue
        bullet = _BULLET_RE.match(line)
        if in_issues and bullet:
            issues.append(bullet.group(1))
        elif in_issues and line.strip() and not issues:
            # plain line right under "ISSUES:" counts as the first issue
            issues.append(line.strip())

    if not issues and not approved:
        reasoning_parts.insert(0, "ревьюер отклонил модуль без списка замечаний")

    reasoning = " ".join(part for part in reasoning_parts if part).strip()
    return JudgeVerdict(
        approved=approved,
        score=score,
        issues=tuple(issues[:max_issues]),
        reasoning=reasoning,
        parsed=parsed,
        raw=text,
    )


class Judge:
    """Review gate over any LLMPort-shaped object (usually a RouterPort)."""

    def __init__(self, llm, config: Optional[JudgeConfig] = None) -> None:
        self._llm = llm
        self._config = config or JudgeConfig()

    @property
    def config(self) -> JudgeConfig:
        return self._config

    def review(
        self,
        task: str,
        code: str,
        diagnostics: Sequence[str] = (),
        reference: str = "",
    ) -> JudgeVerdict:
        """Ask the judge about one generated module; RLLError propagates.

        With a non-empty `reference` the judge runs in the reference-aware
        mode: it sees the gold solution and compares semantics (the stricter
        L2 protocol of SWE-bench-BSL v0.4). The reference is a JUDGE-ONLY
        input — callers must never leak it into generator prompts.
        """

        user = judge_review_prompt(task, code, diagnostics, reference=reference)
        system = (
            JUDGE_REFERENCE_SYSTEM_PROMPT
            if reference.strip()
            else JUDGE_SYSTEM_PROMPT
        )
        messages = [ChatMessage.system(system), ChatMessage.user(user)]

        kwargs: dict = {}
        if self._config.temperature is not None:
            kwargs["temperature"] = self._config.temperature
        if self._config.max_tokens is not None:
            kwargs["max_tokens"] = self._config.max_tokens

        result: CompletionResult = self._llm.complete(messages, **kwargs)
        verdict = parse_judge_response(
            result.text, max_issues=self._config.max_issues
        )
        return replace(
            verdict,
            model=getattr(result, "model", "") or "",
            latency_ms=result.latency_ms,
        )


def judge_feedback(verdict: JudgeVerdict) -> list[str]:
    """Judge issues as fix-prompt feedback lines (reasoning if no issues)."""

    if verdict.issues:
        return list(verdict.issues)
    if verdict.reasoning:
        return [f"Обоснование ревьюера: {verdict.reasoning}"]
    return ["ревьюер отклонил модуль без объяснений"]
