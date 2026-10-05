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

Token-free variant (candidates already produced by a live eval run with
`--save-report`): reuse them instead of re-running the loop:
    python scripts/judge_calibration.py prepare \
        --from-reports ../../runs/judge-v04/p01.json ../../runs/judge-v04/p02.json
    # or an explicit task list (intersection with the reports):
    python scripts/judge_calibration.py prepare --from-reports p*.json \
        --ids func-is-prime,table-create-catalog
Only task id + final code are read from the reports — judge verdicts and
exec outcomes are never extracted, so blind labeling stays blind.
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


def collect_from_reports(paths: list[str]) -> dict[str, str]:
    """Read eval report JSONs and map task_id -> final candidate code.

    Entries may be comma-joined ("a.json,b.json") for shell convenience.
    Only `id` and `code` are read — judge/exec fields stay out on purpose:
    the candidates file must not leak judge decisions to the annotator.
    Later reports win when the same task id appears twice.
    """

    import json

    codes: dict[str, str] = {}
    for raw in paths:
        for chunk in str(raw).split(","):
            name = chunk.strip()
            if not name:
                continue
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            for task in data.get("tasks", []):
                task_id = task.get("id")
                code = task.get("code")
                if task_id and code:
                    codes[str(task_id)] = str(code)
    return codes


def _candidate_rows(sample: list, codes: dict[str, str]) -> list[dict]:
    return [
        {
            "task_id": task.id,
            "category": task.category,
            "difficulty": task.difficulty,
            "prompt": task.prompt,
            "reference": task.reference,
            "code": codes[task.id],
        }
        for task in sample
    ]


def _write_calibration_files(
    candidates: list[dict], candidates_path: Path, labels_path: Path
) -> None:
    candidates_path.write_text(_yaml_dump(candidates), encoding="utf-8")
    labels_path.write_text(
        _yaml_dump([{"task_id": c["task_id"], "human": "", "note": ""} for c in candidates]),
        encoding="utf-8",
    )
    print(f"\ncandidates: {candidates_path}")
    print(f"labels:     {labels_path} — fill `human: pass|fail` BLIND (see module docstring)")
    print("then run:   python scripts/judge_calibration.py run")


def _select_sample(args: argparse.Namespace, tasks: list) -> list:
    """Sample selection shared by the live and report-based paths."""

    if args.ids:
        wanted = [chunk.strip() for chunk in args.ids.split(",") if chunk.strip()]
        known = {t.id for t in tasks}
        unknown = [i for i in wanted if i not in known]
        if unknown:
            raise SystemExit(f"unknown task ids: {', '.join(unknown)}")
        order = {task_id: index for index, task_id in enumerate(wanted)}
        return sorted((t for t in tasks if t.id in order), key=lambda t: order[t.id])
    return sample_tasks(tasks, n=args.n, seed=args.seed)


def cmd_prepare(args: argparse.Namespace) -> int:
    tasks = load_tasks(bundled_tasks_path())
    WORKDIR.mkdir(exist_ok=True)

    candidates_path = WORKDIR / CANDIDATES_FILE
    labels_path = WORKDIR / LABELS_FILE
    if candidates_path.exists() and not args.force:
        print(f"{candidates_path} exists — pass --force to overwrite (labels are lost!)")
        return 1

    sample = _select_sample(args, tasks)

    if args.from_reports:
        codes = collect_from_reports(args.from_reports)
        if not codes:
            print("no candidate code found in the given reports")
            return 1
        if args.ids:
            absent = [t.id for t in sample if t.id not in codes]
            if absent:
                print(f"no candidate code in reports for: {', '.join(absent)}")
                return 1
            usable = sample
        else:
            usable = [t for t in sample if t.id in codes]
            if not usable:
                print("sample and reports do not intersect — check --n/--seed or report paths")
                return 1
            skipped = len(sample) - len(usable)
            if skipped:
                print(f"note: {skipped} sampled task(s) not in reports — skipped")
        _write_calibration_files(_candidate_rows(usable, codes), candidates_path, labels_path)
        print(f"source: eval reports ({len(args.from_reports)} file arg(s), "
              f"{len(codes)} candidate(s) available)")
        print(f"sample: {len(usable)} task(s)")
        return 0

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
    codes: dict[str, str] = {}
    for index, task in enumerate(sample, start=1):
        print(f"[{index:>2}/{len(sample)}] {task.id} ...", flush=True)
        result = loop.run(task.prompt)
        codes[task.id] = result.code or ""
    _write_calibration_files(_candidate_rows(sample, codes), candidates_path, labels_path)
    print(f"source: live loop ({len(sample)} task(s))")
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
        judge_mark = "pass" if verdict.approved else "fail"
        print(f"{task_id:<32} human={labeled['human']:<4} judge={judge_mark}")

    if missing:
        head = ', '.join(missing[:8]) + ('...' if len(missing) > 8 else '')
        print(f"\nunlabeled tasks ({len(missing)}): {head}")
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
    good = metrics["precision"] >= 0.70 and metrics["recall"] >= 0.70
    verdict_line = (
        "OK (>= 0.70)" if good else "BELOW 0.70 — the judge needs work before the pilot"
    )
    print(f"acceptance:     {verdict_line}")
    if args.report:
        Path(args.report).write_text(
            _yaml_dump({"prompt_hash": prompt_hash(), **metrics}), encoding="utf-8"
        )
        print(f"report: {args.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="L2 judge calibration (roadmap 2.1, D2)")
    parser.add_argument(
        "stage", choices=["prepare", "run"], help="prepare candidates or compute metrics"
    )
    parser.add_argument("--n", type=int, default=30, help="sample size (default 30)")
    parser.add_argument("--seed", type=int, default=42, help="sampling seed (default 42)")
    parser.add_argument("--force", action="store_true", help="overwrite existing candidates")
    parser.add_argument(
        "--from-reports",
        nargs="+",
        default=None,
        metavar="REPORT.json",
        help="take candidates from eval report JSON files (comma-joined lists "
        "allowed) instead of running the live loop — spends no tokens",
    )
    parser.add_argument(
        "--ids",
        default=None,
        metavar="ID[,ID...]",
        help="explicit task id list (order preserved); filters both the live "
        "sample and the report-based selection",
    )
    parser.add_argument("--candidates", default=None, help="custom candidates yaml path")
    parser.add_argument("--labels", default=None, help="custom labels yaml path")
    parser.add_argument("--report", default=None, help="write metrics yaml here")
    args = parser.parse_args()
    if args.stage == "prepare":
        return cmd_prepare(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
