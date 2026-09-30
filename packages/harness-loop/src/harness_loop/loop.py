"""BslAgentLoop — the L0/L1 backpressure loop.

    task -> LLM (LLMPort) -> extract BSL -> BslVerifier.verify_module_text
                        ^                                    |
                        +--- fix prompt: code + diagnostics -+

The loop is a thin orchestrator: no framework, no extra dependencies —
it only wires two existing ports (russian-llm-pack, bsl-verify) and keeps
every decision inspectable in the returned LoopResult.

Why not DeepAgents/LangGraph in v0.1: planning and subagents are not
consumed by this loop yet (repo rule: do not over-engineer). LLMPort and
BslVerifier are exactly the seams a future DeepAgents engine would
consume, so growing into one requires no rework of this contract.

Stop conditions:
    - verifier policy passed            -> LoopResult(passed=True)
    - max_iterations exhausted          -> failure_reason="budget_exhausted"
    - RLLError from the LLM port        -> failure_reason="llm_error"
    - BslVerifyError from the verifier  -> failure_reason="verifier_error"
      (environment problems: no java / no jar / analyze timeout)
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from bsl_verify import BslVerifyError, BslVerifier, Severity, VerifyResult
from russian_llm_pack import ChatMessage, RLLError, Router

from .extract import extract_bsl_code
from .prompt import SYSTEM_PROMPT, fix_prompt, task_prompt
from .types import IterationLog, LoopConfig, LoopResult

IterationCallback = Callable[[IterationLog], None]

_SEVERITY_RANK = {
    Severity.ERROR: 0,
    Severity.WARNING: 1,
    Severity.INFORMATION: 2,
    Severity.HINT: 3,
}

NO_CODE_NOTE = (
    "ответ модели не содержит распознаваемого BSL-кода: нет блока ```bsl и "
    "текст не похож на модуль; верни код в блоке ```bsl ... ```"
)


class RouterPort:
    """LLMPort-shaped adapter over Router: pins one routing chain.

    Router.complete() takes the task name as its first argument; the loop
    speaks pure LLMPort. This little adapter is the bridge — and the
    single place where routing (fallback, retries) enters the loop.
    """

    def __init__(self, router: Router, task: Optional[str] = None, model: Optional[str] = None):
        self._router = router
        self._task = task
        self._model = model
        self.name = f"router:{task or router.config.default_task}"

    @property
    def task(self) -> Optional[str]:
        return self._task

    def complete(self, messages, *, model: Optional[str] = None, **kwargs):
        return self._router.complete(self._task, messages, model=self._model or model, **kwargs)

    def stream(self, messages, *, model: Optional[str] = None, **kwargs):
        yield from self._router.stream(self._task, messages, model=self._model or model, **kwargs)


class BslAgentLoop:
    """Generate -> verify -> fix loop over one LLMPort and one BslVerifier."""

    def __init__(
        self,
        llm,
        verifier: BslVerifier,
        config: Optional[LoopConfig] = None,
    ) -> None:
        self._llm = llm
        self._verifier = verifier
        self._config = config or LoopConfig()

    @property
    def config(self) -> LoopConfig:
        return self._config

    def run(
        self,
        task: str,
        context: str = "",
        on_iteration: Optional[IterationCallback] = None,
    ) -> LoopResult:
        """Run the loop for a natural-language task; never raises domain errors."""

        config = self._config
        iterations: list[IterationLog] = []
        code = ""
        diagnostics: list[str] = []

        for index in range(1, config.max_iterations + 1):
            if index == 1:
                user = task_prompt(task, context)
            else:
                user = fix_prompt(task, context, code, diagnostics)
            messages = [ChatMessage.system(SYSTEM_PROMPT), ChatMessage.user(user)]

            try:
                result = self._llm.complete(messages, **self._llm_kwargs())
            except RLLError as exc:
                return LoopResult(
                    passed=False,
                    code=code,
                    iterations=iterations,
                    failure_reason="llm_error",
                    error=str(exc),
                )

            log = IterationLog(
                index=index,
                model=getattr(result, "model", "") or "",
                prompt_tokens=result.usage.input_tokens,
                completion_tokens=result.usage.output_tokens,
                llm_latency_ms=result.latency_ms,
            )

            extracted = extract_bsl_code(result.text)
            if not extracted:
                log.code_extracted = False
                log.note = NO_CODE_NOTE
                diagnostics = [NO_CODE_NOTE]
                iterations.append(log)
                self._notify(on_iteration, log)
                continue  # fix prompt will ask the model to answer properly

            log.code_extracted = True
            code = extracted

            started = time.monotonic()
            try:
                verdict = self._verifier.verify_module_text(
                    code, filename=config.filename
                )
            except BslVerifyError as exc:
                log.verify_ms = (time.monotonic() - started) * 1000.0
                iterations.append(log)
                self._notify(on_iteration, log)
                return LoopResult(
                    passed=False,
                    code=code,
                    iterations=iterations,
                    failure_reason="verifier_error",
                    error=str(exc),
                )

            log.verify_ms = verdict.duration_ms or (time.monotonic() - started) * 1000.0
            log.verified = verdict.passed
            log.errors = verdict.errors
            log.warnings = verdict.warnings
            log.infos = verdict.informations
            log.hints = verdict.hints
            log.diagnostics = format_diagnostics(
                verdict, config.filename, config.max_feedback_lines
            )
            iterations.append(log)
            self._notify(on_iteration, log)
            diagnostics = log.diagnostics

            if verdict.passed:
                return LoopResult(passed=True, code=code, iterations=iterations)

        return LoopResult(
            passed=False,
            code=code,
            iterations=iterations,
            failure_reason="budget_exhausted",
        )

    # -- internals -----------------------------------------------------------

    def _llm_kwargs(self) -> dict:
        kwargs: dict = {}
        if self._config.temperature is not None:
            kwargs["temperature"] = self._config.temperature
        if self._config.max_tokens is not None:
            kwargs["max_tokens"] = self._config.max_tokens
        return kwargs

    @staticmethod
    def _notify(on_iteration: Optional[IterationCallback], log: IterationLog) -> None:
        if on_iteration is None:
            return
        try:
            on_iteration(log)
        except Exception:  # noqa: BLE001 — progress hooks must never break the loop
            pass


def format_diagnostics(
    verdict: VerifyResult, filename: str, limit: int
) -> list[str]:
    """Render verifier diagnostics as feedback lines for the model.

    Errors first (that is what the model must fix), then warnings, then the
    rest; original position order is preserved inside each severity. Capped
    at `limit` lines with an explicit "... и ещё N" tail so the model knows
    the list was truncated, not complete.
    """

    items = sorted(
        verdict.iter_diagnostics(),
        key=lambda pair: _SEVERITY_RANK.get(pair[1].severity, 9),
    )
    lines = [diagnostic.format(filename) for _, diagnostic in items]
    if len(lines) > limit:
        hidden = len(lines) - limit
        return lines[:limit] + [f"... и ещё {hidden} диагностик (не показаны)"]
    return lines
