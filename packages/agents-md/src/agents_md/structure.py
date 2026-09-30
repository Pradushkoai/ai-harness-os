"""Directory scanner: bounded, hidden-dir-free, kind-aware comments.

The scan is deliberately shallow (top level + one level inside `src/`
for 1C projects) and bounded (max 2000 files total) — AGENTS.md needs a
readable map, not a full index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .types import KIND_1C_EDT, KIND_1C_XML, StructureEntry

SKIP_DIRS = {
    ".git", ".github", ".idea", ".vscode", ".settings", ".eggs",
    "__pycache__", "node_modules", "venv", ".venv", "env",
    "build", "dist", "target", "bin", "obj", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "htmlcov", ".DS_Store",
}

MAX_FILES = 2000

# 1C metadata directory names -> Russian comments (EDT and XML dumps share them).
_1C_DIR_COMMENTS = {
    "Catalogs": "Справочники",
    "Documents": "Документы",
    "InformationRegisters": "Регистры сведений",
    "AccumulationRegisters": "Регистры накопления",
    "AccountingRegisters": "Регистры бухгалтерии",
    "Constants": "Константы",
    "Enums": "Перечисления",
    "Reports": "Отчёты",
    "DataProcessors": "Обработки",
    "CommonModules": "Общие модули",
    "CommonForms": "Общие формы",
    "CommonTemplates": "Общие макеты",
    "CommonPictures": "Общие картинки",
    "Roles": "Роли",
    "ScheduledJobs": "Регламентные задания",
    "ExchangePlans": "Планы обмена",
    "ChartsOfCharacteristicTypes": "Планы видов характеристик",
    "ChartsOfAccounts": "Планы счетов",
    "SettingsStorages": "Хранилища настроек",
    "Tasks": "Задачи",
    "BusinessProcesses": "Бизнес-процессы",
    "Subsystems": "Подсистемы",
    "FunctionalOptions": "Функциональные опции",
    "DefinedTypes": "Определяемые типы",
    "CommandGroups": "Группы команд",
    "Interfaces": "Интерфейсы",
    "Styles": "Стили",
    "Languages": "Языки",
    "Sessions": "Сессии",
    "EventSubscriptions": "Подписки на события",
    "FilterCriteria": "Критерии отбора",
}

# Known top-level dirs for generic/python/js projects.
_COMMON_DIR_COMMENTS = {
    "src": "исходный код",
    "app": "исходный код приложения",
    "lib": "библиотека",
    "tests": "тесты",
    "test": "тесты",
    "docs": "документация",
    "examples": "примеры",
    "scripts": "скрипты",
    "tools": "инструменты",
    "config": "конфигурация",
    "migrations": "миграции БД",
    "features": "BDD-сценарии (Vanessa Automation и т.п.)",
    "external": "внешние обработки/печатные формы",
    "packages": "пакеты монорепозитория",
}


def _count_files(directory: Path) -> tuple[int, dict[str, int]]:
    """Bounded recursive count: (total, per-extension counts)."""

    total = 0
    exts: dict[str, int] = {}
    stack = [directory]
    while stack and total < MAX_FILES:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if total >= MAX_FILES:
                break
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
            else:
                total += 1
                ext = entry.suffix.lower() or "(без расширения)"
                exts[ext] = exts.get(ext, 0) + 1
    return total, exts


def _marker(exts: dict[str, int]) -> str:
    """The dominant meaningful extension, e.g. '12 .bsl'."""

    interesting = {e: n for e, n in exts.items() if n >= 3 or e in (".bsl", ".os")}
    if not interesting:
        top = sorted(exts.items(), key=lambda kv: -kv[1])[:1]
        interesting = dict(top)
    if not interesting:
        return ""
    ext, count = sorted(interesting.items(), key=lambda kv: -kv[1])[0]
    return f"{count} {ext}"


def _comment_for(dirname: str, kind: str) -> str:
    if kind in (KIND_1C_EDT, KIND_1C_XML):
        if dirname in _1C_DIR_COMMENTS:
            return _1C_DIR_COMMENTS[dirname]
    if dirname in _COMMON_DIR_COMMENTS:
        return _COMMON_DIR_COMMENTS[dirname]
    if kind in (KIND_1C_EDT, KIND_1C_XML):
        return "метаданные конфигурации"
    return "см. содержимое"


def scan_structure(root: Path, kind: str) -> list[StructureEntry]:
    """Top-level directory entries with counts, comments and markers."""

    entries: list[StructureEntry] = []
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS
                      and not p.name.startswith("."))
    except OSError:
        return entries

    for directory in dirs:
        total, exts = _count_files(directory)
        comment = _comment_for(directory.name, kind)
        entries.append(
            StructureEntry(
                name=directory.name,
                comment=comment,
                file_count=total,
                marker=_marker(exts),
            )
        )
    return entries


def render_tree(name: str, entries: list[StructureEntry], max_entries: int = 15) -> str:
    """ASCII tree for the Architecture section, bounded to `max_entries` lines."""

    lines = [f"{name}/"]
    shown = entries[:max_entries]
    for i, entry in enumerate(shown):
        branch = "└──" if i == len(shown) - 1 else "├──"
        lines.append(f"{branch} {entry.render()}")
    hidden = len(entries) - len(shown)
    if hidden > 0:
        lines.append(f"    ... и ещё {hidden} директорий")
    return "\n".join(lines)


def src_dir(root: Path, kind: str) -> Optional[Path]:
    """The source directory worth a second-level look (1C projects only)."""

    if kind not in (KIND_1C_EDT, KIND_1C_XML):
        return None
    for candidate in (root / "src", root / "Src"):
        if candidate.is_dir():
            return candidate
    return None
