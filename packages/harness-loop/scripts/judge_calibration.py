"""Judge calibration (roadmap 2.1, phase D2): measured L2, not noise.

Two stages, one script:

    prepare  — stratified sample of N tasks, run the loop over them,
               dump candidates (final code per task) + a labeling
               template for the human annotator.
    run      — read the FILLED labels, ask the judge about every
               candidate, compare with the human verdicts, print
               precision / recall / F1 + the confusion matrix.

The human stage between them is the point: the roadmap is explicit that
phase D cannot be automated away — labels come from a person who has
NOT seen the judge verdicts (blind labeling), otherwise the calibration
measures agreement-with-itself, not quality.

Blind-labeling rules (D1 methodology):
    1. Open candidates.yaml ONLY — it carries no judge verdicts.
    2. For every task compare the candidate with the task's reference
       (эталон) by SEMANTICS: formulas, boundaries, edge cases — not by
       text similarity.
    3. human: pass|fail + one-line note WHY (mandatory for fail).
    4. Do not run the judge until all labels are in.

Usage (from packages/harness-loop):
    python scripts/judge_calibration.py prepare [--n 30] [--seed 42]
    # ... fill judge_labels.yaml by hand ...
    python scripts/judge_calibration.py run [--report out.yaml]
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from harness_loop.evals import bundled_tasks_path, load_tasks
from harness_loop.judge import JUDGE_REFERENCE_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT

WORKDIR = Path("calibration")
CANDIDATES_FILE = "candidates.yaml"
LABELS_FILE = "judge_labels.yaml"


def prompt_hash() -> str:
    """Pin the judge prompt version: metrics are meaningless across prompts."""

    payload = (JUDGE_REFERENCE_SYSTEM_PROMPT + "\x00" + JUDGE_SYSTEM_PROMPT).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def sample_tasks(tasks: list, n: int = 30, seed: int = 42) -> list:
    """Stratified sample: category strata, difficulty spread inside them."""

    rng = random.Random(seed)
    by_category: dict[str, list] = {}
    for task in tasks:
        by_category.setdefault(task.category, []).append(task)
    for bucket in by_category.values():
        # difficulty round-robin inside a category, then keep alphabetical order
        order = {"easy": 0, "medium": 1, "hard": 2}
        bucket.sort(key=lambda t: (order.get(t.difficulty, 3), t.id))
    picked: list = []
    categories = sorted(by_category)
    while len(picked) < n and any(by_category.values()):
        for category in categories:
            bucket = by_category[category]
            if not bucket:
                continue
            take = bucket.pop(rng.randrange(len(bucket)))
            picked.append(take)
            if len(picked) >= n:
                break
    return sorted(picked, key=lambda t: t.id)


def compute_metrics(human: list, judge: list) -> dict:
    """Confusion + precision/recall/F1; `pass` is the positive class."""

    if len(human) != len(judge):
        raise ValueError(f"labels and verdicts must align: {len(human)} != {len(judge)}")
    tp = fp = tn = fn = 0
    for expected, predicted in zip(human, judge):
        positive = predicted is True
        truth = expected is True
        if truth and positive:
            tp += 1
        elif truth and not positive:
            fn += 1
        elif not truth and positive:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(human) if human else 0.0
    return {
        "n": len(human),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "accuracy": round(accuracy, 3),
    }


def _yaml_dump(data) -> str:
    import yaml

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def cmd_prepare(args: argparse.Namespace) -> int:
    tasks = load_tasks(bundled_tasks_path())
    sample = sample_tasks(tasks, n=args.n, seed=args.seed)
    WORKDIR.mkdir(exist_ok=True)

    candidates_path = WORKDIR / CANDIDATES_FILE
    labels_path = WORKDIR / LABELS_FILE
    if candidates_path.exists() and not args.force:
        print(f"{candidates_path} exists — pass --force to overwrite (labels are lost!)")
        return 1

    # The live loop needs a configured LLM; without keys we still emit the
    # sample + template so the annotator can prepare, and fail loudly.
    try:
        from russian_llm_pack import RLLError

        from harness_loop.cli import _build_llm, _build_verifier
        from harness_loop.loop import BslAgentLoop
    except ImportError as exc:  # pragma: no cover
        print(f"deps missing: {exc}")
        return 2

    try:
        llm = _build_llm(argparse.Namespace(config=None, chain=None, model=None))
        verifier = _build_verifier(
            argparse.Namespace(java=None, jar=None, timeout=120.0, max_errors=0, ignore="")
        )
    except RLLError as exc:
        print(f"LLM not configured ({exc}) — set up russian-llm-pack first")
        return 2

    loop = BslAgentLoop(llm=llm, verifier=verifier)
    candidates = []
    for index, task in enumerate(sample, start=1):
        print(f"[{index:>2}/{len(sample)}] {task.id} ...", flush=True)
        result = loop.run(task.prompt)
        candidates.append(
            {
                "task_id": task.id,
                "category": task.category,
                "difficulty": task.difficulty,
                "prompt": task.prompt,
                "reference": task.reference,
                "code": result.code,
            }
        )

    candidates_path.write_text(_yaml_dump(candidates), encoding="utf-8")
    labels_path.write_text(
        _yaml_dump(
            [
                {"task_id": c["task_id"], "human": "", "note": ""}
                for c in candidates
            ]
        ),
        encoding="utf-8",
    )
    print(f"\ncandidates: {candidates_path}")
    print(f"labels:     {labels_path} — fill `human: pass|fail` BLIND (see module docstring)")
    print("then run:   python scripts/judge_calibration.py run")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    import yaml

    candidates_path = Path(args.candidates) if args.candidates else WORKDIR / CANDIDATES_FILE
    labels_path = Path(args.labels) if args.labels else WORKDIR / LABELS_FILE
    if not candidates_path.is_file():
        print(f"no candidates at {candidates_path} — run `prepare` first")
        return 1
    if not labels_path.is_file():
        print(f"no labels at {labels_path} — run `prepare` and fill them in")
        return 1

    candidates = yaml.safe_load(candidates_path.read_text(encoding="utf-8"))
    labels_raw = yaml.safe_load(labels_path.read_text(encoding="utf-8"))
    labels = {entry["task_id"]: entry for entry in labels_raw or []}

    human: list = []
    judge_out: list = []
    tasks_by_id = {t.id: t for t in load_tasks(bundled_tasks_path())}
    missing = []
    from harness_loop.cli import _build_judge

    judge = _build_judge(
        argparse.Namespace(config=None, judge_chain=None, judge_model=None)
    )
    for candidate in candidates:
        task_id = candidate["task_id"]
        labeled = labels.get(task_id)
        if not labeled or str(labeled.get("human", "")).lower() not in ("pass", "fail"):
            missing.append(task_id)
            continue
        task = tasks_by_id[task_id]
        verdict = judge.review(task.prompt, candidate["code"], reference=task.reference)
        human.append(str(labeled["human"]).lower() == "pass")
        judge_out.append(bool(verdict.approved))
        print(f"{task_id:<32} human={labeled['human']:<4} judge={'pass' if verdict.approved else 'fail'}")

    if missing:
        print(f"\nunlabeled tasks ({len(missing)}): {', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}")
        print("calibration needs labels for every candidate — fill judge_labels.yaml")
        return 1

    metrics = compute_metrics(human, judge_out)
    print("\n=== judge calibration (phase D2) ===")
    print(f"judge prompt: {prompt_hash()} (sha256-12 of system+reference prompts)")
    print(
        "confusion:      TP {tp} | FP {fp} | TN {tn} | FN {fn}".format(**metrics)
    )
    print(
        "precision {precision}  recall {recall}  F1 {f1}  accuracy {accuracy}  (n={n})".format(
            **metrics
        )
    )
    verdict_line = "OK (>= 0.70)" if metrics["precision"] >= 0.70 and metrics["recall"] >= 0.70 else "BELOW 0.70 — the judge needs work before the pilot"
    print(f"acceptance:     {verdict_line}")
    if args.report:
        Path(args.report).write_text(
            _yaml_dump({"prompt_hash": prompt_hash(), **metrics}), encoding="utf-8"
        )
        print(f"report: {args.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="L2 judge calibration (roadmap 2.1, D2)")
    parser.add_argument("stage", choices=["prepare", "run"], help="prepare candidates or compute metrics")
    parser.add_argument("--n", type=int, default=30, help="sample size (default 30)")
    parser.add_argument("--seed", type=int, default=42, help="sampling seed (default 42)")
    parser.add_argument("--force", action="store_true", help="overwrite existing candidates")
    parser.add_argument("--candidates", default=None, help="custom candidates yaml path")
    parser.add_argument("--labels", default=None, help="custom labels yaml path")
    parser.add_argument("--report", default=None, help="write metrics yaml here")
    args = parser.parse_args()
    if args.stage == "prepare":
        return cmd_prepare(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
