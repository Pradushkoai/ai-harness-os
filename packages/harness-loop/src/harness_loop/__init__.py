"""harness-loop: the L0/L1 backpressure agent loop for 1C/BSL.

Wires two existing ports into a retry loop:

    task -> LLMPort (russian-llm-pack) -> extract BSL code
         -> BslVerifier.verify_module_text (bsl-verify)
         -> on failure: fix prompt with diagnostics -> next iteration

Parse errors (L0) and ~250 diagnostics (L1) come back in seconds and
cost nothing — the LLM is only asked to fix what the cheap gates found.

Deliberately framework-free for v0.1: the loop is a thin orchestrator,
fully unit-testable with fakes. A future DeepAgents/LangGraph engine
would consume the same two ports, so this code is the reference
behaviour, not a dead end.
"""

from .extract import extract_bsl_code
from .loop import BslAgentLoop, RouterPort
from .prompt import SYSTEM_PROMPT, fix_prompt, task_prompt
from .types import IterationLog, LoopConfig, LoopResult

__version__ = "0.1.0"

__all__ = [
    "BslAgentLoop",
    "RouterPort",
    "SYSTEM_PROMPT",
    "IterationLog",
    "LoopConfig",
    "LoopResult",
    "extract_bsl_code",
    "fix_prompt",
    "task_prompt",
    "__version__",
]
