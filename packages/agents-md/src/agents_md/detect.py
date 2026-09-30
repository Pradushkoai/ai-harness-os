"""Project analyzer: markers -> ProjectInfo.

Kind priority (a project can carry several markers; the kind decides the
AGENTS.md template): 1c-edt > 1c-xml > python > js-ts > generic.

All detection is filesystem-only, bounded and dependency-free: file
existence, shallow globbing, light regex/toml-ish reads. The analyzer
never executes project code and never reads .env or secrets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .structure import scan_structure, src_dir
from .types import (
    KIND_1C_EDT,
    KIND_1C_XML,
    KIND_GENERIC,
    KIND_JS_TS,
    KIND_PYTHON,
    ProjectInfo,
)

_MAX_MDO_GLOB = 300  # bounded rglob for EDT detection


def _find_any(root: Path, names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _rglob_bounded(root: Path, pattern: str, limit: int = _MAX_MDO_GLOB) -> list[Path]:
    found: list[Path] = []
    try:
        for path in root.rglob(pattern):
            found.append(path)
            if len(found) >= limit:
                break
    except OSError:
        pass
    return found


def _monorepo_python_packages(root: Path) -> list[Path]:
    """Immediate child directories carrying pyproject.toml (monorepo packages).

    Bounded to direct children and `packages/*` — a python monorepo has its
    pyproject files one or two levels down, never in the root.
    """

    found: list[Path] = []
    bases = [root, root / "packages"]
    for base in bases:
        if not base.is_dir():
            continue
        try:
            children = sorted(
                p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")
            )
        except OSError:
            continue
        for child in children:
            if (child / "pyproject.toml").is_file():
                found.append(child)
    return found


def _detect_1c_edt(root: Path) -> bool:
    """EDT project: .mdo metadata files under a source dir."""

    mdo = _rglob_bounded(root, "*.mdo")
    if not mdo:
        return False
    # A stray .mdo somewhere is not a project; require a Configuration.mdo
    # or a decent cluster of metadata files.
    return any(p.name == "Configuration.mdo" for p in mdo) or len(mdo) >= 10


def _detect_1c_xml(root: Path) -> bool:
    """Configurator XML dump: Configuration.xml near the root."""

    for depth in ("Configuration.xml", "src/Configuration.xml", "Src/Configuration.xml"):
        if (root / depth).is_file():
            return True
    return False


def _pyproject_bits(root: Path) -> tuple[Optional[str], Optional[str]]:
    """(name, requires-python) from a PEP 621 pyproject.toml — regex, no toml lib."""

    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None, None
    try:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None

    def _section_field(section: str, field: str) -> Optional[str]:
        match = re.search(rf"^\[{re.escape(section)}\]\s*$", text, re.MULTILINE)
        if match is None:
            return None
        rest = text[match.end():]
        next_section = rest.find("\n[")
        rest = rest if next_section == -1 else rest[:next_section]
        field_match = re.search(
            rf"^\s*{re.escape(field)}\s*=\s*[\"']([^\"']+)[\"']", rest, re.MULTILINE
        )
        return field_match.group(1) if field_match else None

    name = _section_field("project", "name")
    python = _section_field("project", "requires-python")
    return name, python


def _detect_test_runner(root: Path, python_packages: list[Path] | None = None) -> Optional[str]:
    if (root / "conftest.py").is_file() or (root / "tox.ini").is_file():
        return "pytest"
    pyprojects = [root / "pyproject.toml"] + [
        pkg / "pyproject.toml" for pkg in (python_packages or [])
    ]
    for pyproject in pyprojects:
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "pytest" in text:
            return "pytest"
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            data = {}
        dev = data.get("devDependencies") or {}
        scripts = data.get("scripts") or {}
        if any(k.startswith("vitest") for k in dev):
            return "vitest"
        if any(k in ("jest",) for k in dev) or "jest" in scripts:
            return "jest"
    for requirements in ("requirements-dev.txt", "requirements_test.txt"):
        path = root / requirements
        if path.is_file():
            try:
                if "pytest" in path.read_text(encoding="utf-8", errors="replace"):
                    return "pytest"
            except OSError:
                pass
    return None


def _detect_ci(root: Path) -> Optional[str]:
    if (root / ".github" / "workflows").is_dir():
        return "GitHub Actions"
    if (root / ".gitlab-ci.yml").is_file():
        return "GitLab CI"
    if (root / ".circleci").is_dir():
        return "CircleCI"
    return None


def _detect_linters(root: Path, kind: str) -> list[str]:
    found: list[str] = []
    if (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file():
        found.append("ruff")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "[tool.ruff" in text:
            found.append("ruff")
        if "[tool.black" in text or "[tool.black]" in text:
            found.append("black")
        if "[tool.mypy" in text:
            found.append("mypy")
    for eslint in (".eslintrc", ".eslintrc.js", ".eslintrc.json", "eslint.config.js"):
        if (root / eslint).is_file():
            found.append("eslint")
            break
    if (root / ".prettierrc").is_file() or (root / ".prettierrc.json").is_file():
        found.append("prettier")
    if kind in (KIND_1C_EDT, KIND_1C_XML):
        found.append("bsl-language-server")
    return sorted(set(found))


def detect_project(root: Path) -> ProjectInfo:
    """Analyze `root` and return a ProjectInfo (kind, markers, structure)."""

    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {root}")

    languages: list[str] = []
    notes: list[str] = []
    name = root.resolve().name or "project"
    python_version: Optional[str] = None

    is_1c_edt = _detect_1c_edt(root)
    is_1c_xml = _detect_1c_xml(root)
    python_packages = _monorepo_python_packages(root)
    is_python = _find_any(
        root, ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")
    ) is not None or bool(python_packages)
    package_json = root / "package.json"
    is_js_ts = package_json.is_file() or (root / "tsconfig.json").is_file()

    if is_1c_edt:
        kind = KIND_1C_EDT
        languages.append("BSL/1С (EDT)")
        notes.append("формат исходников: EDT (.mdo)")
    elif is_1c_xml:
        kind = KIND_1C_XML
        languages.append("BSL/1С (XML-выгрузка Конфигуратора)")
        notes.append("формат исходников: XML-выгрузка Конфигуратора")
    elif is_python:
        kind = KIND_PYTHON
        languages.append("Python")
        py_name, python_version = _pyproject_bits(root)
        if py_name:
            name = py_name
        elif python_packages:
            pkg_names = ", ".join(p.name for p in python_packages[:5])
            notes.append(f"монорепозиторий: python-пакеты ({pkg_names})")
    elif is_js_ts:
        kind = KIND_JS_TS
        languages.append("TypeScript" if (root / "tsconfig.json").is_file() else "JavaScript")
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
            if data.get("name"):
                name = str(data["name"])
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    else:
        kind = KIND_GENERIC
        notes.append("стек не распознан — сгенерирован generic-шаблон, допиши руками")

    info = ProjectInfo(
        root=root,
        name=name,
        kind=kind,
        languages=languages,
        python_version=python_version,
    )
    _fill_common(info, notes)
    return info


def _fill_common(info: ProjectInfo, notes: list[str]) -> None:
    python_packages = _monorepo_python_packages(info.root)
    info.test_runner = _detect_test_runner(info.root, python_packages)
    info.ci = _detect_ci(info.root)
    info.docker = (info.root / "Dockerfile").is_file() or _find_any(
        info.root, ("docker-compose.yml", "docker-compose.yaml", "compose.yaml")
    ) is not None
    info.linters = _detect_linters(info.root, info.kind)
    info.structure = scan_structure(info.root, info.kind)
    info.notes.extend(notes)

    if info.kind in (KIND_1C_EDT, KIND_1C_XML):
        src = src_dir(info.root, info.kind)
        bsl_count = sum(
            1 for _ in _rglob_bounded(info.root, "*.bsl", limit=5000)
        )
        info.notes.append(
            f"модулей .bsl: {bsl_count}" + (f", исходники в {src.name}/" if src else "")
        )
        info.notes.append(
            "статическая проверка: bsl-language-server (analyze); см. пакет bsl-verify"
        )
