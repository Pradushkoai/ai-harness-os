"""Core data types for agents-md."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

KIND_PYTHON = "python"
KIND_JS_TS = "js-ts"
KIND_1C_EDT = "1c-edt"
KIND_1C_XML = "1c-xml"
KIND_GENERIC = "generic"


@dataclass(frozen=True)
class StructureEntry:
    """One top-level directory worth mentioning in the architecture tree."""

    name: str          # directory name
    comment: str       # human hint (RU) — kind-specific where known
    file_count: int    # total files inside (bounded scan)
    marker: str = ""   # dominant content marker, e.g. "12 .bsl" or "8 .py"

    def render(self) -> str:
        marker = f", {self.marker}" if self.marker else ""
        return f"{self.name}/  # {self.comment} ({self.file_count} файлов{marker})"


@dataclass
class ProjectInfo:
    """Everything the analyzer learned about the project."""

    root: Path
    name: str
    kind: str = KIND_GENERIC
    languages: list = field(default_factory=list)      # list[str] markers
    test_runner: Optional[str] = None                  # "pytest" | "vitest" | ...
    ci: Optional[str] = None                           # "GitHub Actions" | "GitLab CI"
    docker: bool = False
    linters: list = field(default_factory=list)   # list[str], e.g. ["ruff"]
    structure: list = field(default_factory=list)      # list[StructureEntry]
    python_version: Optional[str] = None               # requires-python, if detected
    description: Optional[str] = None                  # from pyproject, if detected
    notes: list = field(default_factory=list)          # list[str] extra hints for the agent

    def summary_line(self) -> str:
        parts = [f"kind={self.kind}"]
        if self.languages:
            parts.append("langs=" + ",".join(self.languages))
        if self.test_runner:
            parts.append(f"tests={self.test_runner}")
        if self.ci:
            parts.append(f"ci={self.ci}")
        if self.docker:
            parts.append("docker")
        if self.linters:
            parts.append("lint=" + ",".join(self.linters))
        return f"{self.name}: " + " ".join(parts)
