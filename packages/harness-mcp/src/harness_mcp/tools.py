"""The five harness-mcp tools (roadmap 2.1, step B2) over EXISTING ports.

No new engine logic lives here: every tool is a thin adapter.

    ping            -> package versions + environment availability
    benchmark_info  -> profile of the bundled SWE-bench-BSL task set
    verify_module   -> bsl-verify BslVerifier.verify_module_text (L0)
    run_loop        -> harness-loop BslAgentLoop.run + session cache (B3)
    eval_summary    -> harness-loop run_eval, default-limited to 10 tasks

MCP tool ergonomics rule (roadmap): a tool answers in seconds, not
minutes — hence eval defaults to 10 tasks; the full 70 only by an
explicit `full: true`.

Environment wiring (mirrors the harness-loop CLI):
    RLP_CONFIG      -> RouterConfig YAML path (else the pack's default discovery)
    BSL_JAVA_PATH   -> java executable for bsl-verify (else discovery)
    BSL_JAR_PATH    -> bsl-language-server jar
    OSCRIPT_PATH    -> OneScript engine for the L1 oracle
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import bsl_verify
import harness_loop
import russian_llm_pack
from bsl_verify import BslVerifyError, BslVerifier
from harness_loop import BslAgentLoop, LoopConfig
from harness_loop.evals import bundled_tasks_path, load_tasks, run_eval
from harness_loop.executors import OneScriptRunner
from russian_llm_pack import RLLError, Router
from russian_llm_pack.core.config import RouterConfig, load_config

from .state import SessionState

TOOL_PING = "ping"
TOOL_BENCHMARK_INFO = "benchmark_info"
TOOL_VERIFY_MODULE = "verify_module"
TOOL_RUN_LOOP = "run_loop"
TOOL_EVAL_SUMMARY = "eval_summary"

_EVAL_DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class ToolDescriptor:
    """One entry of tools/list: name, human description, JSON schema."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def to_mcp(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.input_schema,
                "additionalProperties": False,
            },
        }


@dataclass(frozen=True)
class ToolOutcome:
    """Tool-level result: text + isError (execution failed, protocol lived)."""

    text: str
    is_error: bool = False


class HarnessServices:
    """Lazy wiring of the existing packages behind the five tools.

    Factories are injectable so unit tests run the full tool surface
    with fakes (no network, no java, no keys) — same philosophy as
    harness-loop's port tests.
    """

    def __init__(
        self,
        loop_factory: Optional[Callable[[], BslAgentLoop]] = None,
        verifier_factory: Optional[Callable[[], BslVerifier]] = None,
        state: Optional[SessionState] = None,
    ) -> None:
        self._loop_factory = loop_factory or self._build_real_loop
        self._verifier_factory = verifier_factory or self._build_real_verifier
        self.state = state or SessionState()
        self._verifier: Optional[BslVerifier] = None
        self._executor: Optional[OneScriptRunner] = None
        self._executor_probed = False

    # -- environment -------------------------------------------------------

    def versions(self) -> dict:
        from . import __version__  # lazy: __init__ imports this module

        java = shutil_which(os.environ.get("BSL_JAVA_PATH") or "java")
        engine = OneScriptRunner.discover()
        return {
            "harness_mcp": __version__,
            "harness_loop": harness_loop.__version__,
            "bsl_verify": bsl_verify.__version__,
            "russian_llm_pack": russian_llm_pack.__version__,
            "java": "ok" if java else "NOT FOUND (verify_module unavailable)",
            "onescript": engine.name if engine and engine.available() else "NOT FOUND (L1 off)",
        }

    def verifier(self) -> BslVerifier:
        if self._verifier is None:
            self._verifier = self._verifier_factory()
        return self._verifier

    def executor(self) -> Optional[OneScriptRunner]:
        if not self._executor_probed:
            self._executor = OneScriptRunner.discover()
            self._executor_probed = True
        return self._executor

    def loop(self, project_path: str = "") -> BslAgentLoop:
        return self._loop_factory(project_path)

    def tasks(self) -> list:
        return load_tasks(bundled_tasks_path())

    def _build_real_verifier(self) -> BslVerifier:
        return BslVerifier(
            java=os.environ.get("BSL_JAVA_PATH") or None,
            jar=os.environ.get("BSL_JAR_PATH") or None,
        )

    def _build_real_loop(self, project_path: str = "") -> BslAgentLoop:
        path = os.environ.get("RLP_CONFIG")
        if path:
            config = RouterConfig.from_yaml(path)
        else:
            config, _found = load_config()
        router = Router.from_config(config)
        llm = harness_loop.RouterPort(router)
        verifier = self.verifier()
        provider = None
        if project_path:
            provider = harness_loop.resolve_provider()
        return BslAgentLoop(
            llm=llm,
            verifier=verifier,
            config=LoopConfig(),
            context_provider=provider,
            project_path=project_path,
        )


def shutil_which(name: str) -> Optional[str]:
    """shutil.which, import kept local to avoid a module-level cost."""

    import shutil

    return shutil.which(name)


# -- tools -------------------------------------------------------------------------


def tool_ping(services: HarnessServices, args: dict) -> ToolOutcome:
    versions = services.versions()
    lines = ["ai-harness-os MCP server:"]
    for key in ("harness_mcp", "harness_loop", "bsl_verify", "russian_llm_pack"):
        lines.append(f"  {key} {versions[key]}")
    lines.append(f"  java: {versions['java']}")
    lines.append(f"  onescript: {versions['onescript']}")
    return ToolOutcome("\n".join(lines))


def tool_benchmark_info(services: HarnessServices, args: dict) -> ToolOutcome:
    tasks = services.tasks()
    total = len(tasks)
    by_category: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    with_checks = 0
    for task in tasks:
        by_category[task.category] = by_category.get(task.category, 0) + 1
        by_difficulty[task.difficulty] = by_difficulty.get(task.difficulty, 0) + 1
        if task.checks:
            with_checks += 1
    coverage = with_checks / total if total else 0.0
    lines = [
        f"SWE-bench-BSL: {total} tasks, {len(by_category)} categories",
        f"L1 oracle coverage: {with_checks}/{total} ({coverage:.0%}) tasks carry executable checks",
        "categories: " + ", ".join(f"{k} x{v}" for k, v in sorted(by_category.items())),
        "difficulty: " + ", ".join(f"{k} x{v}" for k, v in sorted(by_difficulty.items())),
        "not executable (honest limits): query x5 (no Запрос object in OneScript), "
        "skd x4 (platform-only), struct-merge/struct-to-map x2 (Structure iteration "
        "semantics diverge 1C vs OneScript)",
    ]
    return ToolOutcome("\n".join(lines))


def tool_verify_module(services: HarnessServices, args: dict) -> ToolOutcome:
    bsl_text = args.get("bsl_text")
    if not isinstance(bsl_text, str) or not bsl_text.strip():
        return ToolOutcome("error: argument 'bsl_text' (non-empty string) is required", True)
    filename = args.get("filename") or "module.bsl"
    if not isinstance(filename, str) or not filename.strip():
        return ToolOutcome("error: 'filename' must be a non-empty string", True)
    try:
        result = services.verifier().verify_module_text(bsl_text, filename=filename)
    except BslVerifyError as exc:
        return ToolOutcome(
            "verifier unavailable: "
            + str(exc)
            + " — set BSL_JAVA_PATH / BSL_JAR_PATH or install java + bsl-language-server",
            True,
        )
    lines = [result.summary_line()]
    for file_report, diagnostic in result.iter_diagnostics():
        line = diagnostic.range.start.line + 1  # humans are 1-based
        lines.append(
            f"  {file_report.path}: line {line}: "
            f"{diagnostic.severity.value} {diagnostic.code}: {diagnostic.message}"
        )
    lines.append("L0 verdict: " + ("CLEAN" if result.passed else "FAILED"))
    return ToolOutcome("\n".join(lines))


def tool_run_loop(services: HarnessServices, args: dict) -> ToolOutcome:
    # B3: details of an earlier run, without re-running the loop
    run_id = args.get("run_id")
    if isinstance(run_id, str) and run_id.strip() and not args.get("task"):
        return _run_loop_details(services, run_id)

    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        return ToolOutcome(
            "error: argument 'task' (natural-language task) is required "
            "(or pass run_id to fetch details of an earlier run)",
            True,
        )
    context = args.get("context") or ""
    if not isinstance(context, str):
        return ToolOutcome("error: 'context' must be a string", True)
    project_path = args.get("project_path") or ""
    if not isinstance(project_path, str):
        return ToolOutcome("error: 'project_path' must be a string", True)
    if project_path and not os.path.isdir(project_path):
        return ToolOutcome(f"error: 'project_path' is not a directory: {project_path}", True)
    max_iterations = args.get("max_iterations", 3)
    if (
        not isinstance(max_iterations, int)
        or isinstance(max_iterations, bool)
        or max_iterations < 1
    ):
        return ToolOutcome("error: 'max_iterations' must be an integer >= 1", True)

    try:
        loop = services.loop(project_path)
    except (RLLError, BslVerifyError, FileNotFoundError, ValueError) as exc:
        return ToolOutcome(
            f"loop unavailable: {exc} — configure russian-llm-pack (RLP_CONFIG / default "
            "config) and bsl-verify (java + jar) first",
            True,
        )

    result = loop.run(task, context=context)
    run_id = services.state.new_id("run")
    services.state.put(run_id, {"kind": "run_loop", "task": task, "result": result})

    lines = [f"run_id: {run_id} (pass it back for details)"]
    lines.extend(result.summary_lines())
    lines.append("")
    lines.append("final code:")
    lines.append(result.code if result.code.strip() else "(no code extracted)")
    return ToolOutcome("\n".join(lines))


def _run_loop_details(services: HarnessServices, run_id: str) -> ToolOutcome:
    cached = services.state.get(run_id)
    if cached is None:
        return ToolOutcome(
            f"error: run_id {run_id!r} not found (unknown, expired after 15 min, or evicted)",
            True,
        )
    if cached.get("kind") != "run_loop":
        return ToolOutcome(f"error: {run_id!r} is not a run_loop run", True)
    result = cached["result"]
    lines = [f"run_loop {run_id} — task: {cached['task'][:120]}"]
    for iteration in result.iterations:
        ctx = ""
        if iteration.context_source:
            ctx = f" context={iteration.context_source}:{iteration.context_tokens}t"
        lines.append(
            f"iter {iteration.index}: model={iteration.model or '?'} "
            f"code_extracted={iteration.code_extracted} verified={iteration.verified} "
            f"errors={iteration.errors} warnings={iteration.warnings}{ctx}"
        )
        for diagnostic in iteration.diagnostics:
            lines.append(f"    {diagnostic}")
    if result.judge is not None:
        lines.append(f"judge: approved={result.judge.approved} score={result.judge.score}")
    return ToolOutcome("\n".join(lines))


def tool_eval_summary(services: HarnessServices, args: dict) -> ToolOutcome:
    category = args.get("category")
    if category is not None and not isinstance(category, str):
        return ToolOutcome("error: 'category' must be a string", True)
    difficulty = args.get("difficulty")
    if difficulty is not None and not isinstance(difficulty, str):
        return ToolOutcome("error: 'difficulty' must be a string", True)
    full = args.get("full", False)
    if not isinstance(full, bool):
        return ToolOutcome("error: 'full' must be true/false", True)
    limit = args.get("limit", _EVAL_DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return ToolOutcome("error: 'limit' must be an integer >= 1", True)

    tasks = services.tasks()
    if category:
        tasks = [t for t in tasks if t.category == category]
    if difficulty:
        tasks = [t for t in tasks if t.difficulty == difficulty]
    if not full:
        tasks = tasks[:limit]
    if not tasks:
        return ToolOutcome(
            "no tasks match the filter (see benchmark_info for categories/difficulty)", True
        )

    try:
        loop = services.loop()
    except (RLLError, BslVerifyError, FileNotFoundError, ValueError) as exc:
        return ToolOutcome(
            f"loop unavailable: {exc} — configure russian-llm-pack (RLP_CONFIG / default "
            "config) and bsl-verify (java + jar) first",
            True,
        )

    executor = services.executor()
    report = run_eval(tasks, loop, executor=executor if executor and executor.available() else None)
    run_id = services.state.new_id("eval")
    services.state.put(run_id, {"kind": "eval", "report": report})

    lines = [f"run_id: {run_id} | tasks: {report.total} (full={str(full).lower()})"]
    head = f"L0 {report.resolved_l0}/{report.total}"
    if report.l1_measured:
        head += f" | L1 {report.resolved_l1}/{report.l1_measured}"
    stats = report.judge_stats()
    if stats["judged"]:
        head += f" | L2 {stats['approved']}/{stats['judged']} (avg {stats['avg_score']})"
    lines.append(head)
    for outcome in report.outcomes:
        marker = "ok" if outcome.resolved else "FAIL"
        exec_mark = ""
        if outcome.exec_outcome is not None and outcome.exec_outcome.ran:
            exec_mark = " L1:" + ("ok" if outcome.exec_outcome.passed else "fail")
        lines.append(f"  [{marker}{exec_mark}] {outcome.task.id}")
    return ToolOutcome("\n".join(lines))


# name -> (descriptor, handler); the registry the server dispatches over
def build_registry() -> list[tuple[ToolDescriptor, Callable[[HarnessServices, dict], ToolOutcome]]]:
    return [
        (
            ToolDescriptor(
                name=TOOL_PING,
                description="Health-check: package versions, java and OneScript availability.",
                input_schema={},
            ),
            tool_ping,
        ),
        (
            ToolDescriptor(
                name=TOOL_BENCHMARK_INFO,
                description=(
                    "Profile of the bundled SWE-bench-BSL task set: size, categories, "
                    "difficulty, L1 oracle coverage and honest non-executable limits."
                ),
                input_schema={},
            ),
            tool_benchmark_info,
        ),
        (
            ToolDescriptor(
                name=TOOL_VERIFY_MODULE,
                description=(
                    "Verify a 1C/BSL module text with bsl-language-server (L0): syntax "
                    "errors and diagnostics with line numbers, in seconds."
                ),
                input_schema={
                    "bsl_text": {
                        "type": "string",
                        "description": "Full BSL module text to verify",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Reported filename (default module.bsl)",
                    },
                },
            ),
            tool_verify_module,
        ),
        (
            ToolDescriptor(
                name=TOOL_RUN_LOOP,
                description=(
                    "Generate a BSL module for a task: LLM writes, verifier gates, "
                    "returns final code + iteration log. Pass the returned run_id back "
                    "to fetch details without re-running."
                ),
                input_schema={
                    "task": {"type": "string", "description": "Natural-language task"},
                    "context": {"type": "string", "description": "Extra context (optional)"},
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Project directory to index for context (phase C; "
                            "adapter choice: HARNESS_CONTEXT env)"
                        ),
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "LLM call budget, default 3",
                    },
                    "run_id": {
                        "type": "string",
                        "description": "Fetch details of an earlier run instead",
                    },
                },
            ),
            tool_run_loop,
        ),
        (
            ToolDescriptor(
                name=TOOL_EVAL_SUMMARY,
                description=(
                    "Run the benchmark through the loop, brief summary. Default limit "
                    "10 tasks (MCP tools answer in seconds); full=true runs all 70."
                ),
                input_schema={
                    "category": {"type": "string", "description": "Filter by category"},
                    "difficulty": {"type": "string", "description": "Filter by difficulty"},
                    "limit": {"type": "integer", "description": "Task cap when not full"},
                    "full": {"type": "boolean", "description": "Ignore the limit, run all"},
                },
            ),
            tool_eval_summary,
        ),
    ]
