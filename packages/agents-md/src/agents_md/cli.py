"""CLI for agents-md.

    agents-md init [path] [flags]      analyze project -> AGENTS.md
    agents-md validate [path] [flags]  check AGENTS.md against the basics

Exit codes (repo convention):
    0 — done (generated / valid)
    1 — validation failed
    2 — usage / environment error (bad path, overwrite without --force)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .detect import detect_project
from .generate import AGENTS_FILENAME, generate_agents_md, write_agents_md
from .validate import default_agents_path, validate_agents_md


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agents-md",
        description="AGENTS.md generator: project analyzer + RU templates + validator",
    )
    parser.add_argument("--version", action="version", version=f"agents-md {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="analyze project and generate AGENTS.md")
    # --version on BOTH levels (the bsl-check lesson): root and subparser.
    init.add_argument("--version", action="version", version=f"agents-md {__version__}")
    init.add_argument("path", nargs="?", default=".", help="project root (default: cwd)")
    init.add_argument("--force", action="store_true", help="overwrite existing AGENTS.md")
    init.add_argument("--stdout", action="store_true", dest="to_stdout",
                      help="print to stdout instead of writing the file")

    validate = sub.add_parser("validate", help="validate an existing AGENTS.md")
    validate.add_argument("--version", action="version", version=f"agents-md {__version__}")
    validate.add_argument("path", nargs="?", default=".", help="project root (default: cwd)")
    validate.add_argument("--file", default=None,
                          help="explicit AGENTS.md path (default: <root>/AGENTS.md)")
    return parser


def _run_init(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        print(f"error: не найдена директория проекта: {root}", file=sys.stderr)
        return 2

    try:
        info = detect_project(root)
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    target = root / AGENTS_FILENAME
    if target.is_file() and not args.force and not args.to_stdout:
        print(
            f"error: {target} уже существует; используй --force для перезаписи",
            file=sys.stderr,
        )
        return 2

    print(f"Анализ проекта: {root.resolve()}")
    print(f"  {info.summary_line()}")

    content = generate_agents_md(info)
    if args.to_stdout:
        print()
        print(content, end="")
        return 0

    path = write_agents_md(root, content)
    print(f"Сгенерировано: {path} ({len(content.encode('utf-8'))} bytes)")
    print("Проверь, поправь под реальность и закоммить. Валидация: agents-md validate")
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    root = Path(args.path)
    target = Path(args.file) if args.file else default_agents_path(root)
    result = validate_agents_md(target)
    print("\n".join(result.summary_lines()))
    return 0 if result.valid else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Console entry point `agents-md <command> ...` (reads sys.argv itself)."""

    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    parsed = parser.parse_args(args)
    if parsed.command == "init":
        return _run_init(parsed)
    if parsed.command == "validate":
        return _run_validate(parsed)
    parser.error(f"unknown command: {parsed.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
