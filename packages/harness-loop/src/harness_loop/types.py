"""Core data types for harness-loop.

The loop is a pure orchestrator over two ports:
    - an LLMPort (russian-llm-pack) that generates BSL code,
    - a BslVerifier (bsl-verify) that gates it (backpressure L0/L1).

Everything here is plain data: no I/O, no provider imports — which keeps
the loop unit-testable with fakes (repo rule: tests without the world).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class LoopConfig:
    """Loop behaviour knobs.

    Attributes:
        max_iterations: total LLM call budget (initial attempt + fixes). Default 3.
        filename: module filename passed to verify_module_text and shown in the
            diagnostic lines fed back to the model.
        max_feedback_lines: cap on diagnostic lines in the fix prompt — keeps
            the prompt from exploding on noisy modules.
        temperature: optional override forwarded to the LLM port.
        max_tokens: optional override forwarded to the LLM port.
        context_budget: token budget for the project context provider (phase C);
            only meaningful together with a context_provider + project_path.
    """

    max_iterations: int = 3
    filename: str = "module.bsl"
    max_feedback_lines: int = 25
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    context_budget: int = 8000  # token budget for the project context (phase C)

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {self.max_iterations}")
        if self.max_feedback_lines < 1:
            raise ValueError(
                f"max_feedback_lines must be >= 1, got {self.max_feedback_lines}"
            )


@dataclass
class IterationLog:
    """One generate-verify pass of the loop."""

    index: int  # 1-based
    model: str = ""
    code_extracted: bool = False
    verified: Optional[bool] = None
    errors: int = 0
    warnings: int = 0
    informations: int = 0
    hints: int = 0
    diagnostics: list = field(default_factory=list)  # list[str], capped
    note: str = ""  # e.g. "no BSL code extracted" / judge unavailable
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_latency_ms: float = 0.0
    verify_ms: float = 0.0
    judge_verdict: Optional[bool] = None  # True pass / False fail / None not run
    judge_issues: list = field(default_factory=list)  # list[str], capped
    judge_ms: float = 0.0
    judge_samples: int = 0  # self-consistency votes (0 = judge not run here)
    judge_agreement: float = 0.0  # share of votes behind the verdict (1.0 unanimous)
    context_source: str = ""  # builtin | mcp — who filled the context (phase C)
    context_tokens: int = 0  # its size, for observability

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "model": self.model,
            "code_extracted": self.code_extracted,
            "verified": self.verified,
            "errors": self.errors,
            "warnings": self.warnings,
            "informations": self.informations,
            "hints": self.hints,
            "diagnostics": list(self.diagnostics),
            "note": self.note,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_latency_ms": round(self.llm_latency_ms, 1),
            "verify_ms": round(self.verify_ms, 1),
            "judge_verdict": self.judge_verdict,
            "judge_issues": list(self.judge_issues),
            "judge_ms": round(self.judge_ms, 1),
            "judge_samples": self.judge_samples,
            "judge_agreement": round(self.judge_agreement, 2),
            "context_source": self.context_source,
            "context_tokens": self.context_tokens,
        }


@dataclass
class LoopResult:
    """Outcome of one loop run: iterations, final code, verdict.

    failure_reason is empty on success; otherwise one of:
        budget_exhausted — max_iterations reached, code still failing
        judge_rejected   — verifier passed, but the judge veto survived the budget
        llm_error        — the LLM port failed (no keys / chain exhausted)
        verifier_error   — bsl-verify environment failure (no java / no jar)

    judge is the LAST judge verdict when a judge ran at all: the approving
    verdict on success, or the last veto when the rejection survived the
    budget (v0.5: previously vetoes were dropped and judge stayed None —
    eval reports lost the score/issues of rejected tasks).
    judge_error is set when the judge LLM failed — in that case the code
    passed the verifier and passed=True is kept (the verifier stays the
    authority; a judge outage must not discard working code), the outage
    is surfaced loudly instead of being silently ignored.
    """

    passed: bool
    code: str
    iterations: list = field(default_factory=list)  # list[IterationLog]
    failure_reason: str = ""
    error: Optional[str] = None
    judge: Optional["JudgeVerdict"] = None  # noqa: F821 — resolved at runtime
    judge_error: Optional[str] = None

    @property
    def total_prompt_tokens(self) -> int:
        return sum(i.prompt_tokens for i in self.iterations)

    @property
    def total_completion_tokens(self) -> int:
        return sum(i.completion_tokens for i in self.iterations)

    @property
    def total_llm_ms(self) -> float:
        return sum(i.llm_latency_ms for i in self.iterations)

    @property
    def total_verify_ms(self) -> float:
        return sum(i.verify_ms for i in self.iterations)

    @property
    def total_judge_ms(self) -> float:
        return sum(i.judge_ms for i in self.iterations)

    def summary_lines(self) -> list[str]:
        """Human/agent-readable report, one line per iteration."""

        status = "PASSED" if self.passed else "FAILED"
        head = f"BSL loop: {status} after {len(self.iterations)} iteration(s)"
        if not self.passed and self.failure_reason:
            head += f" — {self.failure_reason}"
        tokens = (
            f"tokens: {self.total_prompt_tokens} in + "
            f"{self.total_completion_tokens} out"
        )
        timing = (
            f"llm {self.total_llm_ms / 1000.0:.1f}s, "
            f"verify {self.total_verify_ms / 1000.0:.1f}s"
        )
        if self.judge is not None:
            timing += f", judge {self.total_judge_ms / 1000.0:.1f}s"
        lines = [f"{head} [{tokens}; {timing}]"]
        for it in self.iterations:
            if not it.code_extracted:
                desc = it.note or "no BSL code extracted"
            elif it.verified is None:
                desc = "not verified"
            elif it.verified:
                desc = (
                    f"verify PASSED ({it.errors} error, {it.warnings} warning, "
                    f"{it.informations} info)"
                )
            else:
                desc = (
                    f"verify FAILED ({it.errors} error, {it.warnings} warning, "
                    f"{it.informations} info)"
                )
            if it.judge_verdict is True:
                desc += " judge PASS"
            elif it.judge_verdict is False:
                desc += f" judge FAIL ({len(it.judge_issues)} замечаний)"
            model = f" [{it.model}]" if it.model else ""
            lines.append(f"  iter {it.index}{model}: {desc}")
        if self.judge is not None:
            score = f", score {self.judge.score}" if self.judge.score is not None else ""
            lines.append(f"  judge: {'PASS' if self.judge.approved else 'FAIL'}{score}")
            if not self.judge.parsed:
                lines.append("  judge: ответ не в формате — вердикт не распознан (запрета нет)")
        if self.judge_error:
            lines.append(f"  judge unavailable: {self.judge_error}")
        if self.error:
            lines.append(f"  error: {self.error}")
        return lines

    def to_dict(self) -> dict:
        """JSON-safe representation (used by `harness-loop run --json`)."""

        payload = {
            "passed": self.passed,
            "failure_reason": self.failure_reason,
            "error": self.error,
            "iterations": [i.to_dict() for i in self.iterations],
            "totals": {
                "iterations": len(self.iterations),
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "llm_latency_ms": round(self.total_llm_ms, 1),
                "verify_ms": round(self.total_verify_ms, 1),
                "judge_ms": round(self.total_judge_ms, 1),
            },
            "code": self.code,
            "judge_error": self.judge_error,
        }
        if self.judge is not None:
            payload["judge"] = {
                "approved": self.judge.approved,
                "score": self.judge.score,
                "issues": list(self.judge.issues),
                "reasoning": self.judge.reasoning,
                "parsed": self.judge.parsed,
                "model": self.judge.model,
                "latency_ms": round(self.judge.latency_ms, 1),
            }
        else:
            payload["judge"] = None
        return payload
