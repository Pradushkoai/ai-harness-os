"""Mini SWE-bench-BSL: a task set + runner over the existing loop.

The strategic idea (see the project verdict): nobody has published a BSL
code-generation benchmark. This module is the thin skeleton — task format,
runner, report — so the set can grow task-by-task while the harness
matures. No new package, no framework: the eval is just a loop over
BslAgentLoop.run() with bookkeeping.

Resolution semantics (SWE-bench-like, honest about what we can measure):
    resolved  = loop.passed  (verifier policy satisfied; judge approved
                              if a judge is configured)
    reference solutions are judge-only input since v0.5 (the reference-aware
    judge compares semantics against the etalon); they are NEVER shown to
    the generator — string equality of code is meaningless, and leaking
    the etalon into generation prompts would invalidate the benchmark.

Task file format (YAML):
    version: 1
    tasks:
      - id: func-sum-two-numbers        # unique, stable
        category: function              # function | loop | branching | ...
        difficulty: easy                # easy | medium | hard
        prompt: "..."                   # natural-language task for the model
        context: ""                     # optional extra context
        reference: |                    # reference (gold) solution
          Функция ...
        checks:                         # optional L1 oracle (roadmap 2.1, A2)
          - call: "СуммаДвухЧисел(2, 3)"  # BSL expression evaluated on the
            expect: "5"                  # module; expected printed value
          - call: "ТекстЗапроса()"       # query-text builders use loose
            expect: "ВЫБРАТЬ * Из Т"     # compare: keyword case/whitespace
            case_fold: true               # are normalized away

Checks are generated once by running the REFERENCE solutions through the
real engine (rule "reality > assumptions") — see scripts/gen_checks.py;
hand-written expectations are the fallback, not the default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from .executors import ExecCheck, ExecOutcome, ExecutorPort
from .loop import BslAgentLoop
from .types import LoopResult

BUNDLED_TASKS = Path(__file__).parent / "eval_data" / "tasks_v0.yaml"
TaskCallback = Callable[["TaskOutcome"], None]


def _breakdown_table(lines: list, title: str, groups: dict) -> None:
    """Render a 'solved/total per group' markdown section (helper)."""

    lines.append("")
    lines.append(f"## {title}")
    lines.append("")
    lines.append("| Группа | Решено | Всего | % |")
    lines.append("|---|---|---|---|")
    for label, bucket in groups.items():
        total = bucket["total"]
        resolved = bucket["resolved"]
        rate = f"{100.0 * resolved / total:.0f}%" if total else "—"
        lines.append(f"| {label} | {resolved} | {total} | {rate} |")


@dataclass(frozen=True)
class BslTask:
    """One benchmark task: prompt + reference (+ classification).

    checks carries the L1 oracle: expressions evaluated against the
    generated module in OneScript, with expected printed values. Tasks
    without checks stay L0+L2-only — the honest per-task coverage field
    in the report says exactly which tasks were execution-verified.
    """

    id: str
    prompt: str
    reference: str
    category: str = "general"
    difficulty: str = "medium"
    context: str = ""
    checks: tuple = ()  # tuple[ExecCheck, ...]


@dataclass
class TaskOutcome:
    """One task run through the loop."""

    task: BslTask
    result: LoopResult
    exec_outcome: Optional[ExecOutcome] = None  # L1 oracle, when it ran

    @property
    def resolved(self) -> bool:
        return self.result.passed

    @property
    def resolved_l1(self) -> Optional[bool]:
        """L1 verdict: True/False when the oracle ran, None otherwise.

        Independent of L0 by design: a module can execute correctly while
        still violating the verifier policy (noisy diagnostics) — and
        vice versa. Divergences are signal, not noise.
        """

        if self.exec_outcome is None or not self.exec_outcome.ran:
            return None
        return self.exec_outcome.passed

    @property
    def has_checks(self) -> bool:
        return bool(self.task.checks)

    @property
    def iterations(self) -> int:
        return len(self.result.iterations)

    @property
    def judge_approved(self) -> Optional[bool]:
        """Last judge verdict: True/False when a judge ran, None otherwise."""

        if self.result.judge is not None:
            return self.result.judge.approved
        for iteration in reversed(self.result.iterations):
            if iteration.judge_verdict is not None:
                return iteration.judge_verdict
        return None

    @property
    def judge_score(self) -> Optional[int]:
        """Score of the last judge verdict (None when the judge never ran
        or answered without a parsable score)."""

        if self.result.judge is not None:
            return self.result.judge.score
        return None

    def to_dict(self) -> dict:
        payload = {
            "id": self.task.id,
            "category": self.task.category,
            "difficulty": self.task.difficulty,
            "resolved": self.resolved,
            "resolved_l1": self.resolved_l1,
            "has_checks": self.has_checks,
            "failure_reason": self.result.failure_reason,
            "iterations": self.iterations,
            "prompt_tokens": self.result.total_prompt_tokens,
            "completion_tokens": self.result.total_completion_tokens,
            "llm_latency_ms": round(self.result.total_llm_ms, 1),
            "verify_ms": round(self.result.total_verify_ms, 1),
            "judge_ms": round(self.result.total_judge_ms, 1),
            "judge_approved": self.judge_approved,
            "judge_score": self.judge_score,
            "reference": self.task.reference,
            "code": self.result.code,
        }
        if self.exec_outcome is not None:
            payload["exec"] = self.exec_outcome.to_dict()
        return payload


@dataclass
class EvalReport:
    """Aggregated outcomes + the standard report renderers."""

    outcomes: list = field(default_factory=list)  # list[TaskOutcome]
    judge_mode: str = "none"  # none | plain | reference (set by run_eval)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def resolved(self) -> int:
        return sum(1 for o in self.outcomes if o.resolved)

    @property
    def resolved_l0(self) -> int:
        """L0 = verifier-policy pass. Historical `resolved` is its synonym."""

        return self.resolved

    @property
    def resolved_l1(self) -> int:
        """L1 = execution oracle pass (only measured tasks count)."""

        return sum(1 for o in self.outcomes if o.resolved_l1 is True)

    @property
    def l1_measured(self) -> int:
        """Tasks where the oracle actually ran (engine present + checks)."""

        return sum(1 for o in self.outcomes if o.resolved_l1 is not None)

    @property
    def l1_coverage(self) -> float:
        """Share of tasks WITH executable checks (a property of the set,
        independent of whether the engine was available for this run)."""

        return (
            sum(1 for o in self.outcomes if o.has_checks) / self.total
        ) if self.total else 0.0

    @property
    def pass_rate(self) -> float:
        return (self.resolved / self.total) if self.total else 0.0

    @property
    def total_iterations(self) -> int:
        return sum(o.iterations for o in self.outcomes)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(o.result.total_prompt_tokens for o in self.outcomes)

    @property
    def total_completion_tokens(self) -> int:
        return sum(o.result.total_completion_tokens for o in self.outcomes)

    def failure_reasons(self) -> dict:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.resolved:
                continue
            reason = outcome.result.failure_reason or "unknown"
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _breakdown(self, key: Callable[["TaskOutcome"], str]) -> dict:
        """Group outcomes by a classifier; sorted for stable reports."""

        groups: dict[str, dict[str, int]] = {}
        for outcome in self.outcomes:
            bucket = groups.setdefault(key(outcome), {"total": 0, "resolved": 0})
            bucket["total"] += 1
            if outcome.resolved:
                bucket["resolved"] += 1
        return dict(sorted(groups.items()))

    @property
    def by_category(self) -> dict:
        """Per-category {total, resolved} — where the model is weak."""

        return self._breakdown(lambda o: o.task.category)

    @property
    def by_difficulty(self) -> dict:
        """Per-difficulty {total, resolved} — saturation detector."""

        return self._breakdown(lambda o: o.task.difficulty)

    def judge_stats(self) -> dict:
        """L2 aggregates: how often the judge approved + mean score.

        judged counts outcomes where a judge verdict exists at all;
        judge outage (judge_error) and judge-less runs are excluded.
        """

        judged = [o for o in self.outcomes if o.judge_approved is not None]
        approved = sum(1 for o in judged if o.judge_approved)
        scores = [o.judge_score for o in judged if o.judge_score is not None]
        return {
            "mode": self.judge_mode,
            "judged": len(judged),
            "approved": approved,
            "vetoed": len(judged) - approved,
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        }

    def levels_line(self) -> str:
        """One-line L0/L1/L2 snapshot (roadmap 2.1, A3)."""

        parts = [f"L0 {self.resolved_l0}/{self.total}"]
        if self.l1_measured:
            parts.append(
                f"L1 {self.resolved_l1}/{self.l1_measured} "
                f"(чеки у {round(self.l1_coverage * 100)}% задач)"
            )
        else:
            coverage = round(self.l1_coverage * 100)
            if coverage:
                parts.append(f"L1 не измерялся (чеки у {coverage}% задач, движок недоступен)")
            else:
                parts.append("L1 не измерялся (в наборе нет чеков)")
        stats = self.judge_stats()
        if stats["judged"]:
            parts.append(f"L2 {stats['approved']}/{stats['judged']}")
        return " | ".join(parts)

    def summary_lines(self) -> list[str]:
        rate = f"{self.pass_rate * 100.0:.0f}%"
        head = (
            f"BSL eval: {self.resolved}/{self.total} resolved ({rate}) | "
            f"iterations {self.total_iterations} | tokens "
            f"{self.total_prompt_tokens} in + {self.total_completion_tokens} out"
        )
        stats = self.judge_stats()
        if stats["judged"]:
            score = (
                f", avg score {stats['avg_score']}" if stats["avg_score"] is not None else ""
            )
            head += (
                f" | judge {stats['mode']}: {stats['approved']}/{stats['judged']} "
                f"approved{score}"
            )
        lines = [head, "  " + self.levels_line()]
        by_difficulty = self.by_difficulty
        if by_difficulty:
            parts = [
                f"{label}: {bucket['resolved']}/{bucket['total']}"
                for label, bucket in by_difficulty.items()
            ]
            lines.append("  " + ", ".join(parts))
        for outcome in self.outcomes:
            status = "PASS" if outcome.resolved else "FAIL"
            detail = f"{outcome.iterations} iter"
            if not outcome.resolved:
                reason = outcome.result.failure_reason or "unknown"
                detail += f", {reason}"
            lines.append(
                f"  [{status}] {outcome.task.id} "
                f"({outcome.task.difficulty}, {detail})"
            )
        return lines

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "resolved": self.resolved,
            "resolved_l0": self.resolved_l0,
            "resolved_l1": self.resolved_l1,
            "l1_measured": self.l1_measured,
            "l1_coverage": round(self.l1_coverage, 4),
            "pass_rate": round(self.pass_rate, 4),
            "total_iterations": self.total_iterations,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "failure_reasons": self.failure_reasons(),
            "judge": self.judge_stats(),
            "by_category": self.by_category,
            "by_difficulty": self.by_difficulty,
            "tasks": [o.to_dict() for o in self.outcomes],
        }

    def to_markdown(self) -> str:
        """PR-ready markdown table (shareable progress report)."""

        lines = [
            "# Mini SWE-bench-BSL — отчёт",
            "",
            f"- Задач: **{self.total}**, решено: **{self.resolved}** "
            f"({self.pass_rate * 100.0:.0f}%)",
            f"- Уровни: {self.levels_line()}",
            f"- Итераций суммарно: {self.total_iterations}",
            f"- Токены: {self.total_prompt_tokens} in + "
            f"{self.total_completion_tokens} out",
        ]
        if self.by_difficulty:
            _breakdown_table(lines, "По сложности", self.by_difficulty)
        if self.by_category:
            _breakdown_table(lines, "По категориям", self.by_category)
        stats = self.judge_stats()
        if stats["judged"]:
            score = (
                f"средний балл {stats['avg_score']}" if stats["avg_score"] is not None else ""
            )
            mode = {
                "reference": "против эталонов (reference-aware)",
                "plain": "без эталонов (plain)",
                "none": "",
            }.get(stats["mode"], stats["mode"])
            lines.extend([
                "",
                "## Ревьюер (L2)",
                "",
                f"- Режим: {mode}",
                f"- Вердикты: {stats['approved']}/{stats['judged']} одобрено, "
                f"{stats['vetoed']} вето",
            ])
            if score:
                lines.append(f"- Оценки: {score}")
        lines.extend([
            "",
            "| Статус | Задача | Сложность | Итерации | Причина |",
            "|---|---|---|---|---|",
        ])
        for outcome in self.outcomes:
            status = "✅" if outcome.resolved else "❌"
            reason = "" if outcome.resolved else (
                outcome.result.failure_reason or "unknown"
            )
            lines.append(
                f"| {status} | {outcome.task.id} | {outcome.task.difficulty} "
                f"| {outcome.iterations} | {reason} |"
            )
        return "\n".join(lines)

    def save_json(self, path) -> Path:
        target = Path(path)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def save_markdown(self, path) -> Path:
        target = Path(path)
        target.write_text(self.to_markdown() + "\n", encoding="utf-8")
        return target


def _parse_checks(raw_checks, task_id: str, source) -> tuple:
    """Parse + validate the `checks:` list of one task (A2 schema)."""

    if not isinstance(raw_checks, list):
        raise ValueError(
            f"task {task_id}: checks must be a list, got {type(raw_checks).__name__} ({source})"
        )
    checks = []
    for index, raw in enumerate(raw_checks, start=1):
        if not isinstance(raw, dict) or "call" not in raw:
            raise ValueError(
                f"task {task_id}: check #{index} must be a mapping with 'call' ({source})"
            )
        if "expect" not in raw:
            raise ValueError(
                f"task {task_id}: check #{index} is missing 'expect' ({source})"
            )
        expect = raw["expect"]
        if expect is None:
            expect = ""
        if not isinstance(expect, str):
            raise ValueError(
                f"task {task_id}: check #{index} expect must be a string ({source})"
            )
        case_fold = raw.get("case_fold", False)
        if not isinstance(case_fold, bool):
            raise ValueError(
                f"task {task_id}: check #{index} case_fold must be true/false ({source})"
            )
        proc = raw.get("proc", False)
        if not isinstance(proc, bool):
            raise ValueError(
                f"task {task_id}: check #{index} proc must be true/false ({source})"
            )
        setup = raw.get("setup", "") or ""
        if not isinstance(setup, str):
            raise ValueError(
                f"task {task_id}: check #{index} setup must be a string ({source})"
            )
        try:
            checks.append(
                ExecCheck(
                    call=str(raw["call"]),
                    expect=expect,
                    setup=setup,
                    case_fold=case_fold,
                    proc=proc,
                )
            )
        except ValueError as exc:
            raise ValueError(f"task {task_id}: check #{index}: {exc}") from exc
    return tuple(checks)


def load_tasks(path) -> list[BslTask]:
    """Parse a task YAML file; validates ids and required fields."""

    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    version = data.get("version", 1)
    if version != 1:
        raise ValueError(f"unsupported task file version: {version!r} ({source})")

    raw_tasks = data.get("tasks") or []
    tasks: list[BslTask] = []
    seen_ids: set[str] = set()
    for raw in raw_tasks:
        task_id = str(raw.get("id", "")).strip()
        prompt = str(raw.get("prompt", "")).strip()
        reference = str(raw.get("reference", "") or "").strip()
        if not task_id or not prompt:
            raise ValueError(f"task without id/prompt in {source}")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task id: {task_id} ({source})")
        seen_ids.add(task_id)
        tasks.append(
            BslTask(
                id=task_id,
                prompt=prompt,
                reference=reference,
                category=str(raw.get("category", "general")),
                difficulty=str(raw.get("difficulty", "medium")),
                context=str(raw.get("context", "") or ""),
                checks=_parse_checks(raw.get("checks", []), task_id, source),
            )
        )
    if not tasks:
        raise ValueError(f"no tasks found in {source}")
    return tasks


def bundled_tasks_path() -> Path:
    return BUNDLED_TASKS


def run_eval(
    tasks: list[BslTask],
    loop: BslAgentLoop,
    on_task: Optional[TaskCallback] = None,
    use_reference: bool = True,
    executor: Optional[ExecutorPort] = None,
) -> EvalReport:
    """Run every task through one loop instance; failures are data, not errors.

    use_reference=True (default) feeds every task's gold solution to the
    judge — the reference-aware L2 protocol. The reference never reaches
    the generator (see BslAgentLoop.run). judge_mode in the report marks
    what actually happened: none / plain / reference.

    executor (optional) turns on the L1 oracle for tasks that carry
    checks: the final generated code is executed in OneScript and its
    printed values are compared against the expectations. When the
    engine is unavailable the L1 level is simply not measured — the
    report says so explicitly instead of guessing (ran=False outcomes
    are skips, not verdicts).
    """

    executor_ready = executor is not None and executor.available()
    outcomes: list[TaskOutcome] = []
    for task in tasks:
        reference = task.reference if use_reference else ""
        result = loop.run(task.prompt, context=task.context, reference=reference)
        exec_outcome = None
        if executor_ready and task.checks:
            exec_outcome = executor.run_checks(result.code, list(task.checks))
        outcome = TaskOutcome(task=task, result=result, exec_outcome=exec_outcome)
        outcomes.append(outcome)
        if on_task is not None:
            try:
                on_task(outcome)
            except Exception:  # noqa: BLE001 — progress hooks never break the eval
                pass

    if loop.judge is None:
        judge_mode = "none"
    elif use_reference and any(t.reference for t in tasks):
        judge_mode = "reference"
    else:
        judge_mode = "plain"
    return EvalReport(outcomes=outcomes, judge_mode=judge_mode)
