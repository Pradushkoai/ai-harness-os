"""CLI for harness-loop.

    harness-loop run "task" [flags]   generate -> verify (-> judge) -> fix loop
    harness-loop eval [flags]         mini SWE-bench-BSL: task set -> report
    harness-loop doctor [flags]       check BOTH layers: LLM keys + java/jar

Exit codes (same convention as `rlp` / `bsl-check`):
    0 — loop passed (verifier policy + judge approved)
    1 — loop failed: budget exhausted, judge veto, code still failing
    2 — environment / usage error (no LLM keys, no java/jar, bad config)

`harness-loop eval` exits 0 whenever the run itself completed (even with a
0% pass rate — that is data, not an error) and 2 on environment failures.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from bsl_verify import BslVerifyError, BslVerifier, VerifyPolicy
from bsl_verify.runner import find_jar, find_java, java_version
from russian_llm_pack import RLLError, Router, RouterConfig, load_config

from . import __version__
from .evals import bundled_tasks_path, load_tasks, run_eval
from .judge import Judge, JudgeConfig
from .loop import BslAgentLoop, RouterPort
from .telemetry import telemetry_from_env
from .types import LoopConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-loop",
        description="Agent loop for 1C/BSL: LLM generation gated by bsl-language-server "
        "(backpressure L0/L1)",
    )
    parser.add_argument("--version", action="version", version=f"harness-loop {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the generate-verify-fix loop for a BSL task")
    # --version lives on BOTH levels: `harness-loop --version` is handled by the
    # root parser, `harness-loop run --version` — by this subparser (the bsl-check
    # regression taught us to cover every real invocation path).
    run.add_argument("--version", action="version", version=f"harness-loop {__version__}")
    run.add_argument("task", help="task description in natural language")
    run.add_argument("--context", default=None, help="project context as text")
    run.add_argument("--context-file", default=None, help="read project context from a file")
    run.add_argument("--max-iterations", type=int, default=3, help="LLM call budget (default 3)")
    run.add_argument("--chain", default=None,
                     help="router chain: coding|reasoning|cheap|judge (default: config)")
    run.add_argument("--model", default=None, help="explicit 'provider/model' — bypass routing")
    run.add_argument(
        "--config",
        default=None,
        help="path to rlp.config.yaml (default: RLP_CONFIG / ./rlp.config.yaml / builtin)",
    )
    run.add_argument("--filename", default="module.bsl", help="module filename for diagnostics")
    run.add_argument("--timeout", type=float, default=120.0, help="verifier analyze timeout, s")
    run.add_argument("--max-errors", type=int, default=0, help="policy: allowed errors (default 0)")
    run.add_argument("--ignore", default="", help="comma-separated diagnostic codes to ignore")
    run.add_argument("--temperature", type=float, default=None)
    run.add_argument("--max-tokens", type=int, default=None)
    run.add_argument("--save", default=None, help="write the final code to this file")
    run.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    run.add_argument("--verbose", action="store_true", help="per-iteration progress to stderr")
    run.add_argument("--judge", action="store_true", help="enable the LLM judge (second opinion)")
    run.add_argument("--judge-chain", default=None,
                     help="router chain for the judge LLM (default: same as generation)")
    run.add_argument("--judge-model", default=None,
                     help="explicit 'provider/model' for the judge")
    run.add_argument("--langfuse", action="store_true",
                     help="send telemetry to Langfuse (LANGFUSE_PUBLIC_KEY/SECRET_KEY env)")
    run.add_argument("--java", default=None, help="path to java executable")
    run.add_argument("--jar", default=None, help="path to bsl-language-server.jar")

    ev = sub.add_parser("eval", help="mini SWE-bench-BSL: run a task set, report pass rate")
    ev.add_argument("--version", action="version", version=f"harness-loop {__version__}")
    ev.add_argument("--tasks", default=None,
                    help="path to a tasks YAML (default: bundled v0 set)")
    ev.add_argument("--category", default=None,
                    help="run only these categories (comma-separated: table,query,...)")
    ev.add_argument("--difficulty", default=None,
                    help="run only these difficulties (comma-separated: easy,medium,hard)")
    ev.add_argument("--limit", type=int, default=None, help="run only the first N tasks")
    ev.add_argument("--context", default=None, help="project context as text")
    ev.add_argument("--max-iterations", type=int, default=3, help="LLM call budget per task")
    ev.add_argument("--chain", default=None,
                     help="router chain: coding|reasoning|cheap|judge (default: config)")
    ev.add_argument("--model", default=None, help="explicit 'provider/model' — bypass routing")
    ev.add_argument("--config", default=None, help="path to rlp.config.yaml")
    ev.add_argument("--filename", default="module.bsl", help="module filename for diagnostics")
    ev.add_argument("--timeout", type=float, default=120.0, help="verifier analyze timeout, s")
    ev.add_argument("--max-errors", type=int, default=0, help="policy: allowed errors")
    ev.add_argument("--ignore", default="", help="comma-separated diagnostic codes to ignore")
    ev.add_argument("--temperature", type=float, default=None)
    ev.add_argument("--max-tokens", type=int, default=None)
    ev.add_argument("--judge", action="store_true", help="enable the LLM judge")
    ev.add_argument("--judge-chain", default=None, help="router chain for the judge LLM")
    ev.add_argument("--judge-model", default=None, help="explicit 'provider/model' for the judge")
    ev.add_argument("--langfuse", action="store_true", help="send telemetry to Langfuse")
    ev.add_argument("--save-report", default=None, dest="save_report",
                    help="write the JSON report to this file")
    ev.add_argument("--markdown", default=None, help="write a markdown report to this file")
    ev.add_argument("--json", action="store_true", dest="as_json", help="JSON output only")
    ev.add_argument("--verbose", action="store_true", help="per-task progress to stderr")
    ev.add_argument("--java", default=None, help="path to java executable")
    ev.add_argument("--jar", default=None, help="path to bsl-language-server.jar")

    doctor = sub.add_parser("doctor", help="check both layers: LLM config/keys + java/jar")
    doctor.add_argument("--version", action="version", version=f"harness-loop {__version__}")
    doctor.add_argument("--config", default=None, help="path to rlp.config.yaml")
    doctor.add_argument("--java", default=None, help="path to java executable")
    doctor.add_argument("--jar", default=None, help="path to bsl-language-server.jar")
    return parser


# -- wiring factories (monkeypatched in tests) -----------------------------------


def _build_llm(args: argparse.Namespace):
    """Build the LLM side: RouterConfig -> Router -> RouterPort."""

    if getattr(args, "config", None):
        rc = RouterConfig.from_yaml(args.config)
    else:
        rc, _path = load_config()
    router = Router.from_config(rc)
    return RouterPort(router, task=getattr(args, "chain", None), model=getattr(args, "model", None))


def _build_judge(args: argparse.Namespace):
    """Build the judge: its own RouterPort (own chain) over the same config."""

    if getattr(args, "config", None):
        rc = RouterConfig.from_yaml(args.config)
    else:
        rc, _path = load_config()
    router = Router.from_config(rc)
    port = RouterPort(
        router,
        task=getattr(args, "judge_chain", None),
        model=getattr(args, "judge_model", None),
    )
    return Judge(port, JudgeConfig())


def _build_verifier(args: argparse.Namespace) -> BslVerifier:
    """Build the verifier side: BslVerifier with a policy from CLI flags."""

    ignore = frozenset(c.strip() for c in args.ignore.split(",") if c.strip())
    policy = VerifyPolicy(max_errors=args.max_errors, ignore_codes=ignore)
    return BslVerifier(java=args.java, jar=args.jar, timeout_s=args.timeout, policy=policy)


def _load_context(args: argparse.Namespace) -> str:
    if getattr(args, "context", None) and getattr(args, "context_file", None):
        raise ValueError("use either --context or --context-file, not both")
    if getattr(args, "context_file", None):
        path = Path(args.context_file)
        if not path.is_file():
            raise FileNotFoundError(f"context file not found: {path}")
        return path.read_text(encoding="utf-8")
    return getattr(args, "context", None) or ""


# -- commands ---------------------------------------------------------------------


def _run_run(args: argparse.Namespace) -> int:
    try:
        context = _load_context(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config = LoopConfig(
        max_iterations=args.max_iterations,
        filename=args.filename,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    try:
        llm = _build_llm(args)
        verifier = _build_verifier(args)
        judge = _build_judge(args) if getattr(args, "judge", False) else None
    except (RLLError, BslVerifyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    telemetry = telemetry_from_env() if getattr(args, "langfuse", False) else None
    loop = BslAgentLoop(llm=llm, verifier=verifier, config=config, judge=judge)

    def _progress(log) -> None:
        if telemetry is not None:
            telemetry.on_iteration(log)
        if not getattr(args, "verbose", False):
            return
        if not log.code_extracted:
            desc = log.note or "no BSL code extracted"
        elif log.verified is None:
            desc = "not verified"
        elif log.verified:
            desc = f"PASSED ({log.errors} error, {log.warnings} warning, {log.infos} info)"
        else:
            desc = f"FAILED ({log.errors} error, {log.warnings} warning, {log.infos} info)"
        if log.judge_verdict is True:
            desc += " judge PASS"
        elif log.judge_verdict is False:
            desc += f" judge FAIL ({len(log.judge_issues)} замечаний)"
        model = f" [{log.model}]" if log.model else ""
        print(f"iteration {log.index}/{args.max_iterations}{model}: {desc}", file=sys.stderr)

    try:
        result = loop.run(args.task, context=context, on_iteration=_progress)
    except (RLLError, BslVerifyError) as exc:  # loop converts these; belt & suspenders
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if telemetry is not None:
        telemetry.flush()

    if args.save:
        Path(args.save).write_text(result.code, encoding="utf-8")

    if args.as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("\n".join(result.summary_lines()))
        if args.save:
            print(f"\nsaved: {args.save}")
        elif result.code:
            print(f"\n```bsl\n{result.code.rstrip()}\n```")

    if result.failure_reason in ("llm_error", "verifier_error"):
        return 2
    return 0 if result.passed else 1


def _run_eval(args: argparse.Namespace) -> int:
    try:
        tasks_path = Path(args.tasks) if args.tasks else bundled_tasks_path()
        tasks = load_tasks(tasks_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Filters shrink the set BEFORE --limit: `--difficulty hard --limit 5`
    # means "first 5 hard tasks", not "first 5 tasks if they happen to be hard".
    for flag, field_name in (("category", "category"), ("difficulty", "difficulty")):
        raw = getattr(args, flag, None)
        if not raw:
            continue
        wanted = {v.strip() for v in raw.split(",") if v.strip()}
        if not wanted:
            print(f"error: --{flag} must list at least one value", file=sys.stderr)
            return 2
        tasks = [t for t in tasks if getattr(t, field_name) in wanted]
        if not tasks:
            print(f"error: no tasks match --{flag} {raw}", file=sys.stderr)
            return 2

    if args.limit is not None:
        if args.limit < 1:
            print("error: --limit must be >= 1", file=sys.stderr)
            return 2
        tasks = tasks[: args.limit]

    # CLI --context fills tasks that have no context of their own
    cli_context = getattr(args, "context", None) or ""
    if cli_context:
        tasks = [replace(t, context=t.context or cli_context) for t in tasks]

    config = LoopConfig(
        max_iterations=args.max_iterations,
        filename=args.filename,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    try:
        llm = _build_llm(args)
        verifier = _build_verifier(args)
        judge = _build_judge(args) if getattr(args, "judge", False) else None
    except (RLLError, BslVerifyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    telemetry = telemetry_from_env() if getattr(args, "langfuse", False) else None
    loop = BslAgentLoop(llm=llm, verifier=verifier, config=config, judge=judge)

    def _on_task(outcome) -> None:
        if telemetry is not None:
            for iteration in outcome.result.iterations:
                telemetry.on_iteration(iteration)
        if not getattr(args, "verbose", False):
            return
        status = "PASS" if outcome.resolved else "FAIL"
        reason = f", {outcome.result.failure_reason}" if not outcome.resolved else ""
        print(
            f"task {outcome.task.id}: {status} ({outcome.iterations} iter{reason})",
            file=sys.stderr,
        )

    report = run_eval(tasks, loop, on_task=_on_task)

    if telemetry is not None:
        telemetry.flush()

    if getattr(args, "save_report", None):
        path = report.save_json(args.save_report)
        print(f"report: {path}", file=sys.stderr)
    if getattr(args, "markdown", None):
        path = report.save_markdown(args.markdown)
        print(f"markdown: {path}", file=sys.stderr)

    if getattr(args, "as_json", False):
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("\n".join(report.summary_lines()))
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    problems = 0

    print("LLM layer (russian-llm-pack):")
    try:
        if getattr(args, "config", None):
            rc = RouterConfig.from_yaml(args.config)
        else:
            rc, _path = load_config()
        router = Router.from_config(rc)
        print(f"  config: {rc.source}")
        built = []
        for info in router.status():
            if info.get("unknown"):
                print(f"    {info['name']:<10} unknown preset (custom entry in config)")
                continue
            key_mark = "OK " if info.get("has_key") else "no key (skipped)"
            print(
                f"    {info['name']:<10} {info['base_url']}"
                f"  [{key_mark}: {info['api_key_env']}]"
            )
            if info.get("built"):
                built.append(info["name"])
        default_chain = " -> ".join(str(ref) for ref in rc.chain_for(None))
        print(f"  chain {rc.default_task}: {default_chain}")
        if not built:
            problems += 1
            print("  fix: export at least one provider key, e.g. DEEPSEEK_API_KEY")
    except RLLError as exc:
        problems += 1
        print(f"  config error: {exc}")

    print("Verifier layer (bsl-verify):")
    java = args.java or find_java()
    if java:
        print(f"  java:     {java}")
        print(f"  version:  {java_version(java) or 'unknown (failed to run)'}")
    else:
        problems += 1
        print("  java:     NOT FOUND — install JRE/JDK 17+, or set BSL_JAVA / JAVA_HOME")

    jar = args.jar or find_jar()
    if jar:
        size_mb = Path(jar).stat().st_size / (1024 * 1024)
        print(f"  jar:      {jar} ({size_mb:.0f} MB)")
    else:
        problems += 1
        print("  jar:      NOT FOUND — set BSL_LS_JAR or copy to "
              "~/.bsl-language-server/bsl-language-server.jar")

    print("Telemetry (optional):")
    import os
    langfuse_keys = all(
        os.environ.get(var) for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    )
    if langfuse_keys:
        print("  langfuse: keys present — `--langfuse` will send telemetry")
    else:
        print("  langfuse: keys not set — `--langfuse` is a no-op (fine unless you need it)")

    print(f"\nEnvironment: {'OK — ready to run the loop' if not problems else 'INCOMPLETE'}")
    return 0 if not problems else 2


# -- entry points -----------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Console entry point `harness-loop <command> ...` (reads sys.argv itself)."""

    args = list(sys.argv[1:] if argv is None else argv)
    return _main(args)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run":
        return _run_run(args)
    if args.command == "eval":
        return _run_eval(args)
    if args.command == "doctor":
        return _run_doctor(args)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
