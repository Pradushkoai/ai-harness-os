"""Analyzer tests: every kind detected from tmp projects, markers correct."""

from __future__ import annotations

import pytest

from agents_md import (
    KIND_1C_EDT,
    KIND_1C_XML,
    KIND_GENERIC,
    KIND_JS_TS,
    KIND_PYTHON,
    detect_project,
)


class TestKindDetection:
    def test_python(self, python_project):
        info = detect_project(python_project)
        assert info.kind == KIND_PYTHON
        assert info.name == "my-py-app"  # from pyproject
        assert info.python_version == ">=3.11"
        assert "Python" in info.languages

    def test_1c_edt(self, edt_project):
        info = detect_project(edt_project)
        assert info.kind == KIND_1C_EDT
        assert "BSL/1С (EDT)" in info.languages

    def test_1c_edt_wins_over_python_markers(self, edt_project):
        """A 1C project with some python tooling must still be a 1C project."""

        (edt_project / "requirements.txt").write_text("some-ttool\n", encoding="utf-8")
        info = detect_project(edt_project)
        assert info.kind == KIND_1C_EDT

    def test_1c_xml(self, xml_project):
        info = detect_project(xml_project)
        assert info.kind == KIND_1C_XML
        assert "XML-выгрузка" in info.notes[0]

    def test_1c_xml_wins_over_python(self, xml_project):
        (xml_project / "pyproject.toml").write_text(
            '[project]\nname = "tooling"\n', encoding="utf-8"
        )
        info = detect_project(xml_project)
        assert info.kind == KIND_1C_XML

    def test_js_ts(self, js_project):
        info = detect_project(js_project)
        assert info.kind == KIND_JS_TS
        assert info.name == "my-js-app"  # from package.json
        assert "TypeScript" in info.languages

    def test_generic(self, generic_project):
        info = detect_project(generic_project)
        assert info.kind == KIND_GENERIC
        assert any("не распознан" in note for note in info.notes)

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            detect_project(tmp_path / "no-such-dir")


class TestMarkers:
    def test_python_full_markers(self, python_project):
        info = detect_project(python_project)
        assert info.test_runner == "pytest"
        assert info.ci == "GitHub Actions"
        assert info.docker is True
        assert "ruff" in info.linters

    def test_1c_gets_bsl_linter(self, edt_project):
        info = detect_project(edt_project)
        assert "bsl-language-server" in info.linters
        assert any("модулей .bsl" in note for note in info.notes)

    def test_no_false_markers(self, generic_project):
        info = detect_project(generic_project)
        assert info.test_runner is None
        assert info.ci is None
        assert info.docker is False
        assert info.linters == []


class TestStructureScan:
    def test_structure_entries_present(self, python_project):
        info = detect_project(python_project)
        names = [e.name for e in info.structure]
        assert "src" in names
        assert "tests" in names
        assert ".github" not in names  # hidden/skip dirs excluded

    def test_src_entry_has_python_marker(self, python_project):
        info = detect_project(python_project)
        src = next(e for e in info.structure if e.name == "src")
        assert ".py" in src.marker
        assert src.file_count >= 4

    def test_1c_dirs_get_russian_comments(self, edt_project):
        info = detect_project(edt_project)
        comments = {e.name: e.comment for e in info.structure}
        assert comments["src"] == "исходный код"
        # second-level 1C dirs live inside src/, structure lists top level only

    def test_bounded_scan_is_fast_enough(self, python_project):
        info = detect_project(python_project)
        assert len(info.structure) <= 15  # render cap respected at generation, scan is small
