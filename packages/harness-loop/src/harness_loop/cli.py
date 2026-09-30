"""CLI for harness-loop.

    harness-loop run "task" [flags]   generate -> verify -> fix loop
    harness-loop doctor [flags]       check BOTH layers: LLM keys + java/jar

Exit codes (same convention as `rlp` / `bsl-check`):
    0 — loop passed (verifier policy satisfied)
    1 — loop failed: budget exhausted, code still failing
    2 — environment / usage error (no LLM keys, no java/jar, bad config)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from bsl_verify import BslVerifyError, BslVerifier, VerifyPolicy
from bsl_verify.runner import find_jar, find_java, java_version
from russian_llm_pack import RLLError, Router, RouterConfig, load_config

from . import __version__
from .loop import BslAgentLoop, RouterPort
from .types import LoopConfig, LoopResult


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
    run.add_argument("--java", default=None, help="path to java executable")
    run.add_argument("--jar", default=None, help="path to bsl-language-server.jar")

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
    except (RLLError, BslVerifyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    loop = BslAgentLoop(llm=llm, verifier=verifier, config=config)

    def _progress(log) -> None:
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
        model = f" [{log.model}]" if log.model else ""
        print(f"iteration {log.index}/{args.max_iterations}{model}: {desc}", file=sys.stderr)

    try:
        result = loop.run(args.task, context=context, on_iteration=_progress)
    except (RLLError, BslVerifyError) as exc:  # loop itself converts these; belt & suspenders
        print(f"error: {exc}", file=sys.stderr)
        return 2

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
    if args.command == "doctor":
        return _run_doctor(args)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
