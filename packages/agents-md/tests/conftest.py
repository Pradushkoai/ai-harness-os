"""Hermetic project factories: tmp directories shaped like real projects."""

from __future__ import annotations

from pathlib import Path

import pytest


def make_python_project(tmp: Path) -> Path:
    root = tmp / "my-py-app"
    (root / "src" / "my_app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "my-py-app"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        "\n"
        "[tool.ruff]\n"
        "line-length = 100\n",
        encoding="utf-8",
    )
    (root / "conftest.py").write_text("", encoding="utf-8")
    (root / "src" / "my_app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "my_app" / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "src" / "my_app" / "core.py").write_text(
        "def core():\n    return 2\n", encoding="utf-8"
    )
    (root / "src" / "my_app" / "extra.py").write_text(
        "def extra():\n    return 3\n", encoding="utf-8"
    )
    (root / "tests" / "test_main.py").write_text(
        "def test_run():\n    assert 1\n", encoding="utf-8"
    )
    (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    return root


def make_1c_edt_project(tmp: Path) -> Path:
    root = tmp / "my-1c-edt"
    src = root / "src"
    (src / "Configuration").mkdir(parents=True)
    (src / "CommonModules" / "МойМодуль").mkdir(parents=True)
    (src / "Catalogs" / "Номенклатура").mkdir(parents=True)
    (src / "Documents" / "ЗаказКлиента").mkdir(parents=True)
    (src / "Configuration" / "Configuration.mdo").write_text("<object/>", encoding="utf-8")
    (src / "CommonModules" / "МойМодуль" / "МойМодуль.bsl").write_text(
        "Процедура Тест()\nКонецПроцедуры\n", encoding="utf-8"
    )
    (src / "CommonModules" / "МойМодуль" / "МойМодуль.mdo").write_text(
        "<object/>", encoding="utf-8"
    )
    (src / "Catalogs" / "Номенклатура" / "Ext").mkdir()
    (src / "Documents" / "ЗаказКлиента" / "Ext").mkdir()
    for i in range(10):
        (src / "CommonModules" / "МойМодуль" / f"модуль{i}.bsl").write_text(
            f"Процедура П{i}()\nКонецПроцедуры\n", encoding="utf-8"
        )
    return root


def make_1c_xml_project(tmp: Path) -> Path:
    root = tmp / "my-1c-xml"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "Configuration.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<ConfigData/>\n', encoding="utf-8"
    )
    (src / "Catalogs").mkdir()
    (src / "Catalogs" / "Справочник1.xml").write_text("<Catalog/>", encoding="utf-8")
    (src / "Documents").mkdir()
    (src / "Documents" / "Документ1.bsl").write_text(
        "Процедура ПередЗаписью()\nКонецПроцедуры\n", encoding="utf-8"
    )
    return root


def make_js_project(tmp: Path) -> Path:
    root = tmp / "my-js-app"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        '{\n  "name": "my-js-app",\n  "devDependencies": {"vitest": "^1.0.0"}\n}\n',
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    (root / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    return root


def make_generic_project(tmp: Path) -> Path:
    root = tmp / "my-unknown"
    root.mkdir()
    (root / "README.txt").write_text("hello\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "a.csv").write_text("1;2\n", encoding="utf-8")
    return root


@pytest.fixture
def python_project(tmp_path):
    return make_python_project(tmp_path)


@pytest.fixture
def edt_project(tmp_path):
    return make_1c_edt_project(tmp_path)


@pytest.fixture
def xml_project(tmp_path):
    return make_1c_xml_project(tmp_path)


@pytest.fixture
def js_project(tmp_path):
    return make_js_project(tmp_path)


@pytest.fixture
def generic_project(tmp_path):
    return make_generic_project(tmp_path)


__all__ = [
    "make_1c_edt_project",
    "make_1c_xml_project",
    "make_generic_project",
    "make_js_project",
    "make_python_project",
]
