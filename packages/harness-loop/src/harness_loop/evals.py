"""Mini SWE-bench-BSL: a task set + runner over the existing loop.

The strategic idea (see the project verdict): nobody has published a BSL
code-generation benchmark. This module is the thin skeleton — task format,
runner, report — so the set can grow task-by-task while the harness
matures. No new package, no framework: the eval is just a loop over
BslAgentLoop.run() with bookkeeping.

Resolution semantics (SWE-bench-like, honest about what we can measure):
    resolved  = loop.passed  (verifier policy satisfied; judge approved
                              if a judge is configured)
    reference solutions are REPORTED, never gated on: string equality of
    code is meaningless, the reference exists for human/LLM diffing later.

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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

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
    """One benchmark task: prompt + reference (+ classification)."""

    id: str
    prompt: str
    reference: str
    category: str = "general"
    difficulty: str = "medium"
    context: str = ""


@dataclass
class TaskOutcome:
    """One task run through the loop."""

    task: BslTask
    result: LoopResult

    @property
    def resolved(self) -> bool:
        return self.result.passed

    @property
    def iterations(self) -> int:
        return len(self.result.iterations)

    def to_dict(self) -> dict:
        return {
            "id": self.task.id,
            "category": self.task.category,
            "difficulty": self.task.difficulty,
            "resolved": self.resolved,
            "failure_reason": self.result.failure_reason,
            "iterations": self.iterations,
            "prompt_tokens": self.result.total_prompt_tokens,
            "completion_tokens": self.result.total_completion_tokens,
            "llm_latency_ms": round(self.result.total_llm_ms, 1),
            "verify_ms": round(self.result.total_verify_ms, 1),
            "judge_ms": round(self.result.total_judge_ms, 1),
            "reference": self.task.reference,
            "code": self.result.code,
        }


@dataclass
class EvalReport:
    """Aggregated outcomes + the standard report renderers."""

    outcomes: list = field(default_factory=list)  # list[TaskOutcome]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def resolved(self) -> int:
        return sum(1 for o in self.outcomes if o.resolved)

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

    def summary_lines(self) -> list[str]:
        rate = f"{self.pass_rate * 100.0:.0f}%"
        head = (
            f"BSL eval: {self.resolved}/{self.total} resolved ({rate}) | "
            f"iterations {self.total_iterations} | tokens "
            f"{self.total_prompt_tokens} in + {self.total_completion_tokens} out"
        )
        lines = [head]
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
            "pass_rate": round(self.pass_rate, 4),
            "total_iterations": self.total_iterations,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "failure_reasons": self.failure_reasons(),
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
            f"- Итераций суммарно: {self.total_iterations}",
            f"- Токены: {self.total_prompt_tokens} in + "
            f"{self.total_completion_tokens} out",
        ]
        if self.by_difficulty:
            _breakdown_table(lines, "По сложности", self.by_difficulty)
        if self.by_category:
            _breakdown_table(lines, "По категориям", self.by_category)
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
) -> EvalReport:
    """Run every task through one loop instance; failures are data, not errors."""

    outcomes: list[TaskOutcome] = []
    for task in tasks:
        result = loop.run(task.prompt, context=task.context)
        outcome = TaskOutcome(task=task, result=result)
        outcomes.append(outcome)
        if on_task is not None:
            try:
                on_task(outcome)
            except Exception:  # noqa: BLE001 — progress hooks never break the eval
                pass
    return EvalReport(outcomes=outcomes)
