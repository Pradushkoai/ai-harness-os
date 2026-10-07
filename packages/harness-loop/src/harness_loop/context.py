"""Project context layer (roadmap 2.1, phase C): fill the empty context socket.

The loop always had a `context` parameter — but nothing filled it from a
real project. This module introduces the port and two adapters:

    ContextProviderPort.collect(project_path, task) -> ContextResult

    BuiltInIndexer    — pure stdlib: rglob *.bsl/*.os, extract procedure
                        signatures + metadata references, score modules
                        against the task text, render under a token
                        budget. Works out of the box, zero dependencies.
    McpIndexerBackend — optional subprocess client to an external
                        code-index MCP server (e.g. code-index-mcp):
                        spawn, initialize, tools/list, tools/call; any
                        failure quietly falls back to BuiltInIndexer with
                        a warning note (roadmap: external dependency is
                        optional, never a hard requirement).

Adapter choice is one environment variable:
    HARNESS_CONTEXT = "mcp"     -> McpIndexerBackend (needs CODE_INDEX_MCP_PATH)
    HARNESS_CONTEXT = "builtin" -> BuiltInIndexer (default)
    HARNESS_CONTEXT = "none"    -> no provider at all

Scale knob (for real-configuration pilots):
    HARNESS_CONTEXT_MAX_FILES=N -> how many module heads the builtin indexer
        reads (default 500; a real UT 11 dump carries 7141 .bsl modules —
        the cap bounds IO, path pre-ranking decides WHICH files survive it)

Observability (C3): ContextResult carries source and token size; the
loop copies them into every IterationLog.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

DEFAULT_TOKEN_BUDGET = 8000
_MAX_FILES_SCANNED = 500
_MAX_FILE_BYTES = 64 * 1024
_MAX_SIGNATURES_PER_MODULE = 40
_MAX_METADATA_PER_MODULE = 8

# Live-calibrated tokenizer ratios (probe 2026-10-07, deepseek-chat
# usage.prompt_tokens, texts sampled from the real UT11 dump): cyrillic
# runs ~2.27-2.44 chars/token, ascii ~4.4. The old flat "4 chars per
# token" overshot the cyrillic budget ~1.65x — the E-3 A/B measured a
# nominal 8000-token budget injecting ~13K API tokens (8000*4/2.44).
# Rule: these constants change only with a fresh live probe, never by
# assumption (see README "Token budget calibration").
_TOKENS_PER_CYRILLIC_CHAR = 0.44
_TOKENS_PER_OTHER_CHAR = 0.23

_ENV_BACKEND = "HARNESS_CONTEXT"
_ENV_MCP_PATH = "CODE_INDEX_MCP_PATH"
_ENV_MAX_FILES = "HARNESS_CONTEXT_MAX_FILES"

# procedure/function signatures, export-flag aware (BSL + OneScript)
_SIG_RE = re.compile(
    r"^\s*(Процедура|Функция)\s+([А-Яа-яЁёA-Za-z_][А-Яа-яЁё\w]*)\s*\(([^)]*)\)(.*?Экспорт)?\s*$",
    re.MULTILINE,
)
# 1C metadata references inside module code
_META_RE = re.compile(
    r"\b((?:Справочник|Документ|РегистрСведений|РегистрНакопления|РегистрБухгалтерии|"
    r"Перечисление|ПланВидовХарактеристик|ПланСчетов|ПланОбменов|БизнесПроцесс|Задача)\.[А-ЯЁ][\w]*)\b"
)
_STOP_WORDS = frozenset(
    "функция процедура написать сделай напиши верни вернуть новый если для или или-же "
    "который которая которые чтобы как что где и в на с по из за от до при про без".split()
)


def estimate_tokens(text: str) -> int:
    """Live-calibrated token estimate — budgeting, not billing.

    Cyrillic characters cost ~1.9x more tokenizer slots than ascii for
    RU-LLM tokenizers (measured against deepseek-chat usage data, see
    README "Token budget calibration"). Replaces the old flat
    ``len(text) // 4`` which silently overshot the budget on cyrillic
    project context by ~1.65x.
    """

    cyr = 0
    for ch in text:
        if "\u0400" <= ch <= "\u04FF":
            cyr += 1
    estimate = cyr * _TOKENS_PER_CYRILLIC_CHAR + (len(text) - cyr) * _TOKENS_PER_OTHER_CHAR
    return max(1, round(estimate))


@dataclass(frozen=True)
class ContextResult:
    """What a provider hands to the loop: text + observability."""

    text: str = ""
    source: str = "builtin"  # builtin | mcp
    tokens: int = 0
    modules_scanned: int = 0
    modules_selected: int = 0
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.text.strip()


@runtime_checkable
class ContextProviderPort(Protocol):
    """The context seam: everything the loop needs from an indexer."""

    name: str

    def collect(
        self, project_path: str, task: str, max_tokens: int = DEFAULT_TOKEN_BUDGET
    ) -> ContextResult: ...


def _task_words(task: str) -> list[str]:
    words = re.split(r"[^А-Яа-яЁёA-Za-z0-9]+", task.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOP_WORDS]


@dataclass(frozen=True)
class _ModuleInfo:
    path: str
    signatures: tuple
    metadata: tuple
    score: int = 0
    sig_total: int = 0


class BuiltInIndexer:
    """Zero-dependency project indexer: signatures + metadata under budget."""

    name = "builtin"

    def __init__(
        self,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_files: Optional[int] = None,
        max_file_bytes: int = _MAX_FILE_BYTES,
    ) -> None:
        if token_budget < 100:
            raise ValueError(f"token_budget must be >= 100, got {token_budget}")
        if max_files is None:
            # real configurations carry thousands of modules (UT 11 dump:
            # 7141 .bsl); the cap bounds IO, the env var lets a pilot raise it
            try:
                max_files = int(os.environ.get(_ENV_MAX_FILES, _MAX_FILES_SCANNED))
            except ValueError:
                max_files = _MAX_FILES_SCANNED
        if max_files < 1:
            raise ValueError(f"max_files must be >= 1, got {max_files}")
        self._budget = token_budget
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        # per-instance caches: one eval re-collects context for every task,
        # and re-reading + re-parsing the same 613+ module heads per task is
        # minutes of pure IO on a real dump; the caches live with the indexer
        # instance (one eval run) — staleness across runs is impossible when
        # the provider is rebuilt per invocation (resolve_provider in cli)
        self._files_cache: dict[str, list[Path]] = {}
        self._info_cache: dict[tuple[str, str], _ModuleInfo] = {}

    def collect(
        self, project_path: str, task: str, max_tokens: int = DEFAULT_TOKEN_BUDGET
    ) -> ContextResult:
        budget = max_tokens if max_tokens > 0 else self._budget
        root = Path(project_path)
        if not root.is_dir():
            return ContextResult(note=f"project path not found: {project_path}")

        words = _task_words(task)
        modules = self._scan(root, words)
        if not modules:
            return ContextResult(modules_scanned=0, note="no .bsl/.os modules found")

        # relevance decides the ORDER; the token budget decides the CUT —
        # score-0 modules ride at the tail and only survive in small projects
        scored = sorted(
            (replace(m, score=self._score(m, words)) for m in modules),
            key=lambda m: (-m.score, m.path),
        )

        # header carries the project NAME, not the absolute path: paths differ
        # wildly across platforms (C:\\Users\\... vs /tmp/...) and would make
        # the token budget platform-dependent; module paths are root-relative
        lines = [
            f"== контекст проекта {root.name} (индексер builtin: "
            f"{len(modules)} модулей просканировано, отобрано топ до бюджета ~{budget} токенов) =="
        ]
        selected = 0
        for module in scored:
            block = self._render_module(module)
            if estimate_tokens("\n".join(lines) + block) > budget and selected > 0:
                break
            lines.extend(block.split("\n"))
            selected += 1
        # a single oversized module may still overshoot: trim trailing lines
        # (floor: header + the first line of the best module stay no matter what)
        while estimate_tokens("\n".join(lines)) > budget and len(lines) > 2:
            lines.pop()
        selected = sum(1 for line in lines if line.startswith("--- модуль:"))

        text = "\n".join(lines)
        return ContextResult(
            text=text,
            source="builtin",
            tokens=estimate_tokens(text),
            modules_scanned=len(modules),
            modules_selected=selected,
        )

    # -- internals ---------------------------------------------------------

    def _scan(self, root: Path, words: list[str]) -> list[_ModuleInfo]:
        root_key = str(root)
        files = self._files_cache.get(root_key)
        if files is None:
            files = sorted(
                p
                for p in root.rglob("*")
                if p.suffix.lower() in (".bsl", ".os") and p.is_file()
            )
            self._files_cache[root_key] = files
        # scale guard: reading every head is IO-bound, so when the project is
        # larger than the cap we rank by PATH relevance BEFORE reading — the
        # cap then cuts the least relevant files, not the alphabetically last
        # ones (found on a real UT 11 dump: 7141 modules, cap 500 = 7% seen)
        if len(files) > self._max_files:

            def path_rank(p: Path) -> tuple[int, str]:
                rel = p.relative_to(root).as_posix().lower()
                return (-sum(1 for w in words if w in rel), rel)

            files = sorted(files, key=path_rank)[: self._max_files]
        found: list[_ModuleInfo] = []
        for path in files[: self._max_files]:
            # root-relative, forward slashes — readable for the LLM and
            # independent of where the project happens to live
            relative = path.relative_to(root).as_posix()
            cached = self._info_cache.get((root_key, relative))
            if cached is not None:
                found.append(cached)
                continue
            try:
                # utf-8-sig strips the BOM (every module of a real EDT dump
                # carries one; a leading \ufeff is not matched by \s and the
                # FIRST signature of the file would be lost); errors=ignore
                # drops a multibyte character that the 64 KiB head cut in half
                # instead of injecting U+FFFD garbage into the signatures
                head = path.read_bytes()[: self._max_file_bytes].decode(
                    "utf-8-sig", errors="ignore"
                )
            except OSError:
                continue
            all_matches = _SIG_RE.findall(head)
            matches = all_matches[:_MAX_SIGNATURES_PER_MODULE]
            sig_total = len(all_matches)
            signatures = tuple(
                "    {} {}({}){}".format(
                    kind, name, self._tidy_params(params), " Экспорт" if exported else ""
                )
                for kind, name, params, exported in matches
            )
            metadata = tuple(dict.fromkeys(_META_RE.findall(head)))[:_MAX_METADATA_PER_MODULE]
            info = _ModuleInfo(relative, signatures, metadata, sig_total=sig_total)
            self._info_cache[(root_key, relative)] = info
            found.append(info)
        return found

    @staticmethod
    def _tidy_params(params: str) -> str:
        # real modules keep comments INSIDE parameter lists
        # ("ПриВыгрузкеДанных(СтандартнаяОбработка, // HS\n Структура)");
        # strip them, then flatten whitespace
        no_comments = re.sub(r"//[^\n]*", " ", params)
        cleaned = re.sub(r"\s+", " ", no_comments.replace("Знач ", "").strip())
        return cleaned[:120] + "…" if len(cleaned) > 120 else cleaned

    @staticmethod
    def _score(info: _ModuleInfo, words: list[str]) -> int:
        if not words:
            return 1
        haystack = " ".join(
            [info.path, " ".join(info.signatures), " ".join(info.metadata)]
        ).lower()
        return sum(1 for w in words if w in haystack)

    def _render_module(self, module: _ModuleInfo) -> str:
        header = f"--- модуль: {module.path}"
        if module.score:
            header += f" (релевантность {module.score})"
        lines = [header]
        if module.metadata:
            lines.append("    метаданные: " + ", ".join(module.metadata))
        if module.signatures:
            lines.extend(module.signatures)
        else:
            lines.append("    (экспортируемых сигнатур не найдено)")
        hidden = module.sig_total - len(module.signatures)
        if hidden > 0:
            lines.append(f"    (и ещё {hidden} сигнатур, скрыто лимитом)")
        return "\n".join(lines)


class McpIndexerBackend:
    """Subprocess client to an external code-index MCP server (optional).

    Protocol: the same MCP-over-stdio the repo's own harness-mcp speaks —
    one JSON-RPC 2.0 message per line. The backend spawns the binary,
    handshakes, lists tools and calls the first tool whose name matches
    the search preference (configurable via CODE_INDEX_MCP_TOOLS, comma
    separated). Any failure at any stage returns a fallback ContextResult
    with a warning note — the caller (the loop) then uses BuiltInIndexer.

    Honest v0.1 limit: the external server's tool catalogue is only known
    up to its name; arguments are passed through as {query, path}.
    """

    name = "mcp"

    def __init__(
        self,
        binary: str,
        args: Optional[list] = None,
        timeout_s: float = 30.0,
        preferred_tools: Optional[list[str]] = None,
    ):
        if not binary:
            raise ValueError("binary path is required")
        self._binary = binary
        self._args = list(args or [])
        self._timeout = timeout_s
        self._preferred = preferred_tools or [
            "search", "search_definition", "find_definition", "get_structure", "structure",
        ]

    def collect(
        self, project_path: str, task: str, max_tokens: int = DEFAULT_TOKEN_BUDGET
    ) -> ContextResult:
        if not shutil.which(self._binary) and not Path(self._binary).is_file():
            return ContextResult(source="mcp", note=f"mcp binary not found: {self._binary}")
        try:
            return self._collect(project_path, task, max_tokens)
        except Exception as exc:  # noqa: BLE001 — external process: any failure = fallback
            return ContextResult(
                source="mcp", note=f"mcp indexer failed ({exc}); falling back to builtin"
            )

    def _collect(self, project_path: str, task: str, max_tokens: int) -> ContextResult:
        proc = subprocess.Popen(
            [self._binary] + self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            init = self._roundtrip(
                proc,
                {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                },
            )
            server_name = init.get("result", {}).get("serverInfo", {}).get("name", "unknown")
            listing = self._roundtrip(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = [t.get("name", "") for t in listing.get("result", {}).get("tools", [])]
            tool = self._pick_tool(tools)
            if tool is None:
                return ContextResult(
                    source="mcp",
                    modules_scanned=0,
                    note=f"mcp server '{server_name}' exposes no matching tool out of {len(tools)}",
                )
            call = self._roundtrip(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": {"query": task, "path": project_path}},
                },
            )
            return self._result_from_call(call, tool, max_tokens)
        finally:
            try:
                proc.stdin.close()
                proc.wait(timeout=self._timeout)
            except Exception:  # noqa: BLE001 — best-effort teardown
                proc.kill()

    def _pick_tool(self, tools: list[str]) -> Optional[str]:
        for preference in self._preferred:
            for name in tools:
                if preference.lower() in name.lower():
                    return name
        return None

    def _result_from_call(self, response: dict, tool: str, max_tokens: int) -> ContextResult:
        content = response.get("result", {}).get("content", [])
        text = "\n".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        ).strip()
        if not text:
            return ContextResult(source="mcp", note=f"mcp tool '{tool}' returned no text")
        while estimate_tokens(text) > max_tokens and "\n" in text:
            text = text.rsplit("\n", 1)[0]
        header = f"== контекст проекта (индексер mcp, тулинг {tool}) =="
        return ContextResult(
            text=f"{header}\n{text}",
            source="mcp",
            tokens=estimate_tokens(text),
            modules_selected=1,
            note="",
        )

    @staticmethod
    def _roundtrip(proc: subprocess.Popen, request: dict) -> dict:
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("mcp server closed its stdout")
        return json.loads(line)


def resolve_provider(env: Optional[dict] = None) -> Optional[ContextProviderPort]:
    """One environment variable chooses the adapter (roadmap C1/C2 rule)."""

    source = os.environ if env is None else env
    mode = (source.get(_ENV_BACKEND) or "builtin").strip().lower()
    if mode == "none":
        return None
    if mode == "mcp":
        binary = source.get(_ENV_MCP_PATH, "")
        if binary:
            raw = source.get("CODE_INDEX_MCP_TOOLS", "")
            preferred = [t.strip() for t in raw.split(",") if t.strip()]
            return McpIndexerBackend(binary, preferred_tools=preferred or None)
        # mcp requested but no binary: builtin with a note is better than nothing
    return BuiltInIndexer()


def collect_context(
    provider: ContextProviderPort,
    project_path: str,
    task: str,
    max_tokens: int = DEFAULT_TOKEN_BUDGET,
) -> ContextResult:
    """Collect via the provider; an mcp failure quietly falls back to builtin.

    Roadmap C2 rule: the external dependency is optional — its
    unavailability must never break the loop. The fallback keeps the
    warning in `note` so logs and IterationLog stay honest about what
    actually produced the context.
    """

    result = provider.collect(project_path, task, max_tokens)
    if result.source == "mcp" and result.note:
        builtin_result = BuiltInIndexer().collect(project_path, task, max_tokens)
        if not builtin_result.empty:
            warning = f"mcp недоступен ({result.note}) — использован builtin"
            return replace(builtin_result, note=warning)
        return replace(builtin_result, note=result.note)
    return result
