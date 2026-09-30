"""`rlp` — CLI for manual checks and dogfooding.

Commands:
    rlp check                       show config, providers, key status, chains
    rlp chat "prompt"               one-shot completion through the router
        --task coding|reasoning|cheap|judge
        --model deepseek/deepseek-chat   (bypass routing)
        --system "system prompt"
        --stream                     stream deltas to stdout
        --temperature 0.2 --max-tokens 512
        --verbose                    print routing events as JSONL to stderr

Exit codes: 0 ok, 2 no available model / config error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import __version__
from .core.config import load_config
from .core.router import Router
from .types import ChatMessage, NoAvailableModelError, RLLError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rlp",
        description="Russian LLM Pack — unified LLM port with routing and fallback",
    )
    parser.add_argument("--version", action="version", version=f"rlp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="show config, provider key status and routing chains")

    chat = sub.add_parser("chat", help="one-shot completion through the router")
    chat.add_argument("prompt", help="user prompt text")
    chat.add_argument("--task", default=None, help="task name (default from config)")
    chat.add_argument("--model", default=None, help="explicit 'provider/model' — bypass routing")
    chat.add_argument("--system", default=None, help="optional system prompt")
    chat.add_argument("--stream", action="store_true", help="stream deltas to stdout")
    chat.add_argument("--temperature", type=float, default=None)
    chat.add_argument("--max-tokens", type=int, default=None)
    chat.add_argument("--verbose", action="store_true", help="routing events as JSONL on stderr")
    return parser


def _print_check(router: Router) -> int:
    config = router.config
    print(f"russian-llm-pack v{__version__}")
    print(f"config: {config.source}")
    print()

    print("Providers:")
    for info in router.status():
        if info.get("unknown"):
            print(f"  {info['name']:<10} unknown preset (custom entry in config)")
            continue
        flags = []
        if info.get("experimental"):
            flags.append("experimental")
        key_mark = "OK " if info.get("has_key") else "no key (skipped)"
        print(
            f"  {info['name']:<10} {info['base_url']}"
            f"  [{key_mark}: {info['api_key_env']}]"
        )
        models = ", ".join(info["models"])
        print(f"  {'':<10} models: {models}")
        if flags or info.get("notes"):
            note = "; ".join(filter(None, flags + [info.get("notes", "")]))
            print(f"  {'':<10} note: {note}")
    print()

    print("Routing chains (first available wins, rest are fallback):")
    for task, refs in config.tasks.items():
        chain = " -> ".join(refs)
        marker = " *" if task == config.default_task else ""
        print(f"  {task:<10} {chain}{marker}")
    return 0


def _event_printer(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), file=sys.stderr)


def _cmd_chat(args: argparse.Namespace) -> int:
    config, _path = load_config()
    router = Router.from_config(
        config, on_event=_event_printer if args.verbose else None
    )

    messages = []
    if args.system:
        messages.append(ChatMessage.system(args.system))
    messages.append(ChatMessage.user(args.prompt))

    kwargs = {}
    if args.temperature is not None:
        kwargs["temperature"] = args.temperature
    if args.max_tokens is not None:
        kwargs["max_tokens"] = args.max_tokens

    task = args.task

    try:
        if args.stream:
            for event in router.stream(task, messages, **kwargs):
                sys.stdout.write(event.delta)
                sys.stdout.flush()
            sys.stdout.write("\n")
            return 0

        result = router.complete(task, messages, model=args.model, **kwargs)
    except NoAvailableModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RLLError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    meta = (
        f"[{result.provider}/{result.model}] "
        f"{result.latency_ms / 1000.0:.1f}s, "
        f"tokens in/out: {result.usage.input_tokens}/{result.usage.output_tokens}"
    )
    print(meta, file=sys.stderr)
    print(result.text)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        config, path = load_config()
        router = Router.from_config(config)
        return _print_check(router)
    if args.command == "chat":
        return _cmd_chat(args)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
