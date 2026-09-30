"""CLI for bsl-verify.

    bsl-check <paths...> [flags]     run verification, print report
    bsl-doctor [flags]               environment check: java, jar, hints

Exit codes (same convention as `rlp`):
    0 — policy passed
    1 — policy violations found (errors over the limit)
    2 — environment / usage error (no java, no jar, bad path)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import __version__
from .policy import VerifyPolicy
from .runner import (
    DOWNLOAD_URL,
    JAR_CONVENTIONAL_DIR,
    find_jar,
    find_java,
    jar_search_locations,
    java_version,
)
from .types import BslVerifyError, VerifyResult
from .verifier import BslVerifier, collect_bsl_files


def _common_verifier_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--java", default=None, help="path to java executable")
    parser.add_argument("--jar", default=None, help="path to bsl-language-server.jar")
    parser.add_argument("--timeout", type=float, default=120.0, help="analyze timeout, seconds")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bsl-check",
        description="1C/BSL static verification via bsl-language-server (L0/L1 backpressure)",
    )
    parser.add_argument("--version", action="version", version=f"bsl-check {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="verify files or directories")
    check.add_argument("paths", nargs="+", help=".bsl/.os files or directories")
    check.add_argument("--config", default=None, help="bsl-language-server JSON config (-c)")
    check.add_argument("--max-errors", type=int, default=0, help="policy: allowed errors (default 0)")
    check.add_argument("--max-warnings", type=int, default=None, help="policy: allowed warnings")
    check.add_argument("--ignore", default="", help="comma-separated diagnostic codes to ignore")
    check.add_argument("--only", default="", help="comma-separated codes to count (whitelist)")
    check.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    check.add_argument("--verbose", action="store_true", help="extra details to stderr")
    _common_verifier_args(check)

    doctor = sub.add_parser("doctor", help="check java/jar environment")
    _common_verifier_args(doctor)
    return parser


def _policy_from_args(args: argparse.Namespace) -> VerifyPolicy:
    ignore = frozenset(c.strip() for c in args.ignore.split(",") if c.strip())
    only_raw = frozenset(c.strip() for c in args.only.split(",") if c.strip())
    return VerifyPolicy(
        max_errors=args.max_errors,
        max_warnings=args.max_warnings,
        ignore_codes=ignore,
        only_codes=only_raw or None,
    )


def _run_check_pipeline(args: argparse.Namespace) -> VerifyResult:
    """Build a verifier from parsed args and run it (raises BslVerifyError)."""

    policy = _policy_from_args(args)
    verifier = BslVerifier(
        java=args.java,
        jar=args.jar,
        config=args.config,
        timeout_s=args.timeout,
        policy=policy,
    )

    directories = [p for p in args.paths if _looks_like_dir(p)]
    files = collect_bsl_files([p for p in args.paths if not _looks_like_dir(p)])

    results: list[VerifyResult] = []
    if directories:
        for directory in directories:
            results.append(verifier.verify_dir(directory))
    if files:
        results.append(verifier.verify_files([str(f) for f in files]))

    if not results:
        raise BslVerifyError("no .bsl/.os files found in the given paths")
    return _merge_results(results, policy)


def _run_check(args: argparse.Namespace) -> int:
    if getattr(args, "verbose", False):
        from .runner import find_jar, find_java

        java = args.java or find_java() or "<java not found>"
        jar = args.jar or find_jar() or "<jar not found>"
        print(f"[verbose] java: {java}", file=sys.stderr)
        print(f"[verbose] jar:  {jar}", file=sys.stderr)
        print(f"[verbose] timeout: {args.timeout}s, policy: {_policy_from_args(args).describe()}",
              file=sys.stderr)

    try:
        merged = _run_check_pipeline(args)
    except BslVerifyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(merged.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("\n".join(merged.summary_lines()))
    return 0 if merged.passed else 1


def _looks_like_dir(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_dir()


def _merge_results(results: list[VerifyResult], policy: VerifyPolicy) -> VerifyResult:
    """Merge several run results under one policy verdict."""

    if len(results) == 1:
        return results[0]
    files = [f for r in results for f in r.files]
    verdict = policy.evaluate(files)
    return VerifyResult(
        files=files,
        errors=verdict.errors,
        warnings=verdict.warnings,
        informations=verdict.informations,
        hints=verdict.hints,
        passed=verdict.passed,
        violations=list(verdict.violations),
        duration_ms=sum(r.duration_ms for r in results),
        report_date=next((r.report_date for r in results if r.report_date), ""),
        source_dir="; ".join(r.source_dir for r in results if r.source_dir),
        policy=policy.describe(),
    )


def _run_doctor(args: argparse.Namespace) -> int:
    problems = 0

    java = args.java or find_java()
    if java:
        version = java_version(java)
        print(f"java:     {java}")
        print(f"version:  {version or 'unknown (failed to run)'}")
    else:
        problems += 1
        print("java:     NOT FOUND")
        print("          fix: install JRE/JDK 17+, or set BSL_JAVA / JAVA_HOME")

    jar = args.jar or find_jar()
    if jar:
        size_mb = _file_size_mb(jar)
        print(f"jar:      {jar} ({size_mb:.0f} MB)")
    else:
        problems += 1
        print("jar:      NOT FOUND")
        print(f"          fix: download exec.jar from {DOWNLOAD_URL}")
        print(f"          then set BSL_LS_JAR=<path> or copy to "
              f"~/{JAR_CONVENTIONAL_DIR}/bsl-language-server.jar")
        print("          searched:")
        for location in jar_search_locations():
            print(f"            - {location}")

    print(f"\nEnvironment: {'OK — ready to verify' if not problems else 'INCOMPLETE'}")
    return 0 if not problems else 2


def _file_size_mb(path: str) -> float:
    from pathlib import Path

    try:
        return Path(path).stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def main_check(argv: Optional[Sequence[str]] = None) -> int:
    """Console entry point `bsl-check <paths...> [flags]`."""

    args = list(sys.argv[1:] if argv is None else argv)
    return _main(["check"] + args)


def main_doctor(argv: Optional[Sequence[str]] = None) -> int:
    """Console entry point `bsl-doctor [flags]`."""

    args = list(sys.argv[1:] if argv is None else argv)
    return _main(["doctor"] + args)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "check":
        return _run_check(args)
    if args.command == "doctor":
        return _run_doctor(args)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
