"""Generator + validator tests: generated AGENTS.md must be complete and valid."""

from __future__ import annotations

from pathlib import Path

from agents_md import generate_agents_md, validate_agents_md
from agents_md.detect import detect_project
from agents_md.generate import write_agents_md


class TestGeneration:
    def test_generated_file_is_valid(self, python_project):
        info = detect_project(python_project)
        content = generate_agents_md(info, date="2026-09-30")
        write_agents_md(python_project, content)

        result = validate_agents_md(python_project / "AGENTS.md")

        assert result.valid, "\n".join(result.errors)

    def test_all_kinds_generate_valid_files(self, edt_project, xml_project,
                                            js_project, generic_project):
        for root in (edt_project, xml_project, js_project, generic_project):
            info = detect_project(root)
            content = generate_agents_md(info, date="2026-09-30")
            path = write_agents_md(root, content)
            result = validate_agents_md(path)
            assert result.valid, f"{root.name}: {'; '.join(result.errors)}"

    def test_1c_content(self, edt_project):
        info = detect_project(edt_project)
        content = generate_agents_md(info, date="2026-09-30")

        assert "1С:Предприятие 8.3" in content
        assert "bsl-language-server" in content
        assert "its.1c.ru/db/v8std" in content
        assert "Кириллица" in content or "кириллица" in content
        assert "## Architecture" in content
        assert "src/" in content or "src/" in content

    def test_python_content(self, python_project):
        info = detect_project(python_project)
        content = generate_agents_md(info, date="2026-09-30")

        assert "my-py-app" in content
        assert "pip install" in content
        assert ">=3.11" in content
        assert "pytest -q" in content
        assert "ruff" in content

    def test_generic_warns_to_edit(self, generic_project):
        info = detect_project(generic_project)
        content = generate_agents_md(info, date="2026-09-30")
        assert "руками" in content

    def test_tree_rendered(self, python_project):
        info = detect_project(python_project)
        content = generate_agents_md(info, date="2026-09-30")
        assert "├──" in content or "└──" in content

    def test_size_under_cascade_limit(self, edt_project):
        info = detect_project(edt_project)
        content = generate_agents_md(info, date="2026-09-30")
        assert len(content.encode("utf-8")) < 32 * 1024


class TestValidation:
    def test_missing_file(self, tmp_path):
        result = validate_agents_md(tmp_path / "AGENTS.md")
        assert result.valid is False
        assert any("not found" in e for e in result.errors)

    def test_missing_sections(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text("# AGENTS.md\n\nТолько заголовок и ничего больше.\n" * 5, encoding="utf-8")
        result = validate_agents_md(path)
        assert result.valid is False
        missing = " ".join(result.errors)
        assert "Project Overview" in missing
        assert "Architecture" in missing

    def test_russian_section_aliases_accepted(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text(
            "# AGENTS.md\n\n"
            "## Обзор проекта\n\nТекст обзора достаточно длинный, чтобы пройти минимум.\n\n"
            "## Установка\n\nШаги установки проекта описаны здесь подробно.\n\n"
            "## Архитектура\n\nСтруктура проекта и слои описаны здесь полностью.\n\n"
            "## Тестирование\n\nКак запускать тесты и что проверять перед коммитом.\n",
            encoding="utf-8",
        )
        result = validate_agents_md(path)
        assert result.valid, "\n".join(result.errors)

    def test_oversize_detected(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        filler = "## Architecture\n\n" + ("деталь " * 4000) + "\n"
        path.write_text(
            "# AGENTS.md\n\n## Project Overview\n\nОбзор проекта достаточно длинный.\n\n"
            "## Setup\n\nУстановка проекта шаг за шагом.\n\n"
            + filler
            + "## Testing\n\nТесты запускаются так.\n",
            encoding="utf-8",
        )
        result = validate_agents_md(path)
        assert result.valid is False
        assert any("32 KiB" in e for e in result.errors)

    def test_valid_file_passes(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text(
            "# AGENTS.md — пример\n\n"
            "## Project Overview\n\nРабочий проект со всеми разделами и внятным описанием.\n\n"
            "## Setup\n\nУстановка простая и описана по шагам.\n\n"
            "## Architecture\n\nСлои и структура проекта кратко.\n\n"
            "## Testing\n\npytest -q и проверка линтером.\n",
            encoding="utf-8",
        )
        result = validate_agents_md(path)
        assert result.valid is True
        assert result.summary_lines()[0].startswith("AGENTS.md: VALID")


class TestRegeneration:
    def test_generate_then_validate_roundtrip_all_kinds(self, python_project, edt_project):
        for root in (python_project, edt_project):
            info = detect_project(root)
            write_agents_md(root, generate_agents_md(info))
            assert (root / "AGENTS.md").is_file()
