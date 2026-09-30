"""Validator: does an AGENTS.md satisfy the basics of the standard?

Checks (light, useful, no external tools):
    - exists and decodes as UTF-8;
    - within the 32 KiB cascade limit (agents.md / Codex cascade semantics);
    - has the sections an agent actually looks for: Overview, Setup,
      Architecture, Testing;
    - headings are ATX style (`#`), the file is non-trivial (> 200 bytes).

Exit-code-friendly: returns a ValidationResult, the CLI maps it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .generate import AGENTS_FILENAME

MAX_BYTES = 32 * 1024  # the cascade limit agents.md consumers apply
MIN_BYTES = 200

REQUIRED_SECTIONS = ("Project Overview", "Setup", "Architecture", "Testing")

# Russian heading variants an agent-friendly AGENTS.md may use instead
# of the canonical English section names.
_SECTION_ALIASES = {
    "Project Overview": ("обзор", "о проекте", "overview", "проект"),
    "Setup": ("установ", "инсталл", "setup", "install", "окружени"),
    "Architecture": ("архитектур", "структур", "arch", "дерево проекта"),
    "Testing": ("тест", "testing", "проверк"),
}


@dataclass
class ValidationResult:
    path: Path
    valid: bool = True
    errors: list = field(default_factory=list)   # list[str]
    warnings: list = field(default_factory=list)  # list[str]
    size_bytes: int = 0

    def summary_lines(self) -> list[str]:
        status = "VALID" if self.valid else "INVALID"
        lines = [f"AGENTS.md: {status} ({self.path}, {self.size_bytes} bytes)"]
        lines.extend(f"  ! {e}" for e in self.errors)
        lines.extend(f"  ? {w}" for w in self.warnings)
        return lines


def validate_agents_md(path: Path) -> ValidationResult:
    """Validate one AGENTS.md file; never raises on content problems."""

    path = Path(path)
    result = ValidationResult(path=path)

    if not path.is_file():
        result.valid = False
        result.errors.append(f"file not found: {path}")
        return result

    try:
        raw = path.read_bytes()
    except OSError as exc:
        result.valid = False
        result.errors.append(f"unreadable: {exc}")
        return result

    result.size_bytes = len(raw)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        result.valid = False
        result.errors.append("not valid UTF-8 — пересохрани файл в UTF-8")
        return result

    if result.size_bytes > MAX_BYTES:
        result.errors.append(
            f"{result.size_bytes} bytes > {MAX_BYTES} (32 KiB cascade limit) — "
            "сократи или вынеси в module-level AGENTS.md"
        )
    if result.size_bytes < MIN_BYTES:
        result.warnings.append(
            f"подозрительно короткий ({result.size_bytes} bytes) — "
            "похоже на заглушку, а не на правила проекта"
        )

    sections = {
        line.lstrip("#").strip().lower()
        for line in text.splitlines()
        if line.startswith("#")
    }
    for required in REQUIRED_SECTIONS:
        required_lower = required.lower()
        aliases = tuple(a.lower() for a in _SECTION_ALIASES.get(required, ()))
        if not any(
            s == required_lower or s.startswith(required_lower) or s.startswith(aliases)
            for s in sections
        ):
            result.errors.append(f"нет раздела: {required}")

    result.valid = not result.errors
    return result


def default_agents_path(root: Path) -> Path:
    return Path(root) / AGENTS_FILENAME
