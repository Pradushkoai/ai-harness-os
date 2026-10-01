"""harness-loop: the L0/L1 backpressure agent loop for 1C/BSL (+ L2 judge).

Wires the existing ports into a retry loop:

    task -> LLMPort (russian-llm-pack) -> extract BSL code
         -> BslVerifier.verify_module_text (bsl-verify)
         -> on failure: fix prompt with diagnostics -> next iteration
         -> on verifier pass + judge configured: Judge.review (second
            opinion; a veto sends review issues back to the model)

Parse errors (L0) and ~250 diagnostics (L1) come back in seconds and
cost nothing — the LLM is only asked to fix what the cheap gates found.

Also here (v0.2): optional Langfuse telemetry over the on_event /
on_iteration seams (stdlib-only HTTP client) and the mini SWE-bench-BSL
eval skeleton (task YAML + runner + report) — the strategic asset:
nobody has published a BSL code-generation benchmark yet.

Deliberately framework-free: the loop is a thin orchestrator, fully
unit-testable with fakes. A future DeepAgents/LangGraph engine would
consume the same ports, so this code is the reference behaviour, not a
dead end.
"""

from .evals import BslTask, EvalReport, TaskOutcome, load_tasks, run_eval
from .extract import extract_bsl_code
from .judge import Judge, JudgeConfig, JudgeVerdict, parse_judge_response
from .loop import BslAgentLoop, RouterPort
from .prompt import SYSTEM_PROMPT, fix_prompt, task_prompt
from .telemetry import LangfuseTelemetry
from .types import IterationLog, LoopConfig, LoopResult

__version__ = "0.3.0"

__all__ = [
    "BslAgentLoop",
    "BslTask",
    "EvalReport",
    "Judge",
    "JudgeConfig",
    "JudgeVerdict",
    "LangfuseTelemetry",
    "RouterPort",
    "SYSTEM_PROMPT",
    "TaskOutcome",
    "IterationLog",
    "LoopConfig",
    "LoopResult",
    "extract_bsl_code",
    "fix_prompt",
    "load_tasks",
    "parse_judge_response",
    "run_eval",
    "task_prompt",
    "__version__",
]
