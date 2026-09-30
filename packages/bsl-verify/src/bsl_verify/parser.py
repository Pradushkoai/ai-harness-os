"""Parser for bsl-language-server `analyze -r json` output.

Contract (verified against v1.0.7):

    {
      "date": "2026-09-30 09:11:05",
      "fileinfos": [
        {
          "path": "file:///abs/path/module.bsl",
          "mdoRef": "...",
          "diagnostics": [
            {
              "code": "ParseError",
              "codeDescription": {"href": "..."},
              "message": "...",
              "range": {"start": {"line": 3, "character": 0},
                         "end":   {"line": 3, "character": 14}},
              "severity": "Error",
              "source": "bsl-language-server",
              "tags": []
            }
          ],
          "metrics": {"procedures": 1, "functions": 0, "lines": 5, ...}
        }
      ],
      "sourceDir": "/abs/path"
    }

The parser is deliberately tolerant: missing sections, absent fields,
unusual severity spellings and plain (non-URI) paths must not crash it —
bsl-language-server evolves, we degrade gracefully.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from .types import (
    BslVerifyError,
    Diagnostic,
    FileMetrics,
    FileReport,
    Position,
    Range,
    Severity,
)


class ReportParseError(BslVerifyError):
    """The analyze output could not be understood at all."""


@dataclass
class RawReport:
    date: str = ""
    source_dir: str = ""
    files: list = field(default_factory=list)  # list[FileReport]


# LSP numeric severity (just in case a future version emits numbers).
_NUMERIC_SEVERITY = {
    1: Severity.ERROR,
    2: Severity.WARNING,
    3: Severity.INFORMATION,
    4: Severity.HINT,
}

_STRING_SEVERITY = {
    "error": Severity.ERROR,
    "warning": Severity.WARNING,
    "information": Severity.INFORMATION,
    "informational": Severity.INFORMATION,
    "info": Severity.INFORMATION,
    "hint": Severity.HINT,
}


def parse_report(data: Any) -> RawReport:
    """Parse analyze output (str/bytes JSON or an already-loaded dict)."""

    if isinstance(data, (str, bytes, bytearray)):
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReportParseError(f"invalid JSON in analyze report: {exc}") from exc
    else:
        parsed = data

    if parsed is None:
        return RawReport()
    if not isinstance(parsed, dict):
        raise ReportParseError(
            f"unexpected analyze report root: {type(parsed).__name__} (expected object)"
        )

    files = [_parse_fileinfo(entry) for entry in parsed.get("fileinfos") or []]
    return RawReport(
        date=str(parsed.get("date") or ""),
        source_dir=str(parsed.get("sourceDir") or ""),
        files=[f for f in files if f is not None],
    )


# -- internals ------------------------------------------------------------------


def _parse_fileinfo(entry: Any) -> Optional[FileReport]:
    if not isinstance(entry, dict):
        return None
    uri = str(entry.get("path") or entry.get("mdoRef") or "")
    diagnostics = [
        _parse_diagnostic(d)
        for d in (entry.get("diagnostics") or [])
        if isinstance(d, dict)
    ]
    diagnostics.sort(key=lambda d: (d.range.start.line, d.range.start.character))
    return FileReport(
        path=uri_to_path(uri),
        diagnostics=diagnostics,
        metrics=_parse_metrics(entry.get("metrics")),
    )


def _parse_diagnostic(raw: dict) -> Diagnostic:
    return Diagnostic(
        range=_parse_range(raw.get("range")),
        severity=_parse_severity(raw.get("severity")),
        code=_string_or_empty(raw.get("code")),
        message=_string_or_empty(raw.get("message")),
        tags=tuple(str(t) for t in (raw.get("tags") or []) if t is not None),
        source=_string_or_empty(raw.get("source")),
    )


def _parse_severity(value: Any) -> Severity:
    if value is None:
        return Severity.INFORMATION
    if isinstance(value, bool):  # guard: bool is int
        return Severity.INFORMATION
    if isinstance(value, int):
        return _NUMERIC_SEVERITY.get(value, Severity.INFORMATION)
    normalized = str(value).strip().lower()
    return _STRING_SEVERITY.get(normalized, Severity.INFORMATION)


def _parse_range(value: Any) -> Range:
    if not isinstance(value, dict):
        return Range()
    return Range(
        start=_parse_position(value.get("start")),
        end=_parse_position(value.get("end")),
    )


def _parse_position(value: Any) -> Position:
    if not isinstance(value, dict):
        return Position()
    try:
        return Position(
            line=max(0, int(value.get("line", 0))),
            character=max(0, int(value.get("character", 0))),
        )
    except (TypeError, ValueError):
        return Position()


def _parse_metrics(value: Any) -> Optional[FileMetrics]:
    if not isinstance(value, dict):
        return None

    def _int(key: str) -> Optional[int]:
        raw = value.get(key)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return FileMetrics(
        procedures=_int("procedures"),
        functions=_int("functions"),
        lines=_int("lines"),
        ncloc=_int("ncloc"),
        comments=_int("comments"),
        statements=_int("statements"),
        cognitive_complexity=_int("cognitiveComplexity"),
        cyclomatic_complexity=_int("cyclomaticComplexity"),
    )


def _string_or_empty(value: Any) -> str:
    return "" if value is None else str(value)


def uri_to_path(uri: str) -> str:
    """Convert a `file://` URI into a normalized filesystem path.

    Handles percent-encoding and Windows drive letters:
        file:///home/x/module.bsl -> /home/x/module.bsl
        file:///D:/work/module.bsl -> D:/work/module.bsl

    Also normalizes `../..` segments: when the analyzed directory lies
    outside the process cwd, bsl-language-server emits the path relative
    to it (verified against v1.0.7), e.g.
        file:///home/z/x/pkg/../../../../../tmp/m.bsl -> /tmp/m.bsl
    """

    if not uri:
        return ""
    if not uri.startswith("file://"):
        return os.path.normpath(uri)
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    # file:///D:/... -> parsed.path == "/D:/..."; keep POSIX absolute intact
    if len(path) > 2 and path[0] == "/" and path[2] == ":" and path[1].isalpha():
        path = path[1:]
    return os.path.normpath(path) if path else path
