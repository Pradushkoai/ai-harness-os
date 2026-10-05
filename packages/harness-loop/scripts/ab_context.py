"""A/B demo: does project context help the loop? (roadmap 2.1, phase C4)

Builds a synthetic 1C-ish project tree (modules drawn from the benchmark
categories), then runs a task subset twice — once bare, once with the
builtin project context — and prints the L0/L1/L2 comparison.

Requires a configured LLM (russian-llm-pack) and, for L1, the OneScript
engine — the same environment `harness-loop eval` needs. Without keys
the script still builds the tree and prints what it *would* run.

Usage (from packages/harness-loop):
    python scripts/ab_context.py [--category structure] [--limit 8] [--keep]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from harness_loop.context import BuiltInIndexer, collect_context
from harness_loop.evals import bundled_tasks_path, load_tasks
from harness_loop.loop import BslAgentLoop

SAMPLE_MODULE = """Процедура {proc}(Параметр) Экспорт
    // категория: {category}
    В = Справочник.Номенклатура;
КонецПроцедуры
"""


def build_synthetic_project(dest: Path, tasks) -> Path:
    """One .bsl module per benchmark category — a plausible-ish 1C tree."""

    categories = sorted({t.category for t in tasks})
    for index, category in enumerate(categories, start=1):
        module = dest / "src" / f"{index:02d}_{category}"
        module.mkdir(parents=True, exist_ok=True)
        (module / f"{category}Модуль.bsl").write_text(
            SAMPLE_MODULE.format(proc=f"Обработать{category.capitalize()}", category=category),
            encoding="utf-8",
        )
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B: context vs no context")
    parser.add_argument("--category", default=None, help="limit tasks to one category")
    parser.add_argument("--difficulty", default=None, help="limit tasks to one difficulty")
    parser.add_argument("--limit", type=int, default=8, help="tasks per arm (default 8)")
    parser.add_argument("--budget", type=int, default=8000, help="context token budget")
    parser.add_argument("--keep", action="store_true", help="keep the synthetic project dir")
    args = parser.parse_args()

    tasks = load_tasks(bundled_tasks_path())
    if args.category:
        tasks = [t for t in tasks if t.category == args.category]
    if args.difficulty:
        tasks = [t for t in tasks if t.difficulty == args.difficulty]
    tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks match the filter")
        return 1

    dest = Path(tempfile.mkdtemp(prefix="ab-context-"))
    try:
        project = build_synthetic_project(dest, tasks)
        print(f"synthetic project: {project} ({len(list(project.rglob('*.bsl')))} modules)")

        indexer = BuiltInIndexer(token_budget=args.budget)
        context = collect_context(indexer, str(project), tasks[0].prompt)
        print(f"context sample ({context.tokens} tokens, {context.modules_selected} modules):")
        sample = "\n".join("  " + line for line in context.text.splitlines()[:12])
        print(sample)

        try:
            from russian_llm_pack import RLLError

            from harness_loop.cli import _build_llm, _build_verifier
        except ImportError as exc:  # pragma: no cover
            print(f"deps missing: {exc}")
            return 2

        try:
            llm = _build_llm(argparse.Namespace(config=None, chain=None, model=None))
            verifier = _build_verifier(
                argparse.Namespace(java=None, jar=None, timeout=120.0, max_errors=0, ignore="")
            )
        except (RLLError, Exception) as exc:
            print(f"environment not ready for the live A/B ({exc}) — tree + context demo above")
            return 0

        from harness_loop.evals import run_eval

        for label, provider, path in (
            ("bare", None, ""),
            ("context", BuiltInIndexer(token_budget=args.budget), str(project)),
        ):
            loop = BslAgentLoop(
                llm=llm, verifier=verifier, context_provider=provider, project_path=path
            )
            report = run_eval(tasks, loop)
            print(
                f"[{label:7s}] L0 {report.resolved_l0}/{report.total} "
                f"iterations {report.total_iterations} "
                f"tokens {report.total_prompt_tokens}+{report.total_completion_tokens}"
            )
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
