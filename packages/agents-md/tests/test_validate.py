"""Unit tests for the AGENTS.md validator (hermetic, tmp files only)."""

from __future__ import annotations

from pathlib import Path

from agents_md import validate as v

# A minimal file that satisfies every check (sections in canonical English).
_VALID_BODY = (
    "# AGENTS.md — demo\n\n"
    "## Project Overview\n\nОбзор проекта: что это и зачем.\n\n"
    "## Setup\n\nУстановка окружения по шагам.\n\n"
    "## Architecture\n\nДерево проекта и слои.\n\n"
    "## Testing\n\nКак запускать тесты.\n\n"
    "- правило раз\n- правило два\n- правило три\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestValidFile:
    def test_ok_file_is_valid(self, tmp_path):
        path = _write(tmp_path, "AGENTS.md", _VALID_BODY)
        result = v.validate_agents_md(path)
        assert result.valid is True
        assert result.errors == []
        assert result.size_bytes == path.stat().st_size

    def test_summary_lines_valid(self, tmp_path):
        path = _write(tmp_path, "AGENTS.md", _VALID_BODY)
        lines = v.validate_agents_md(path).summary_lines()
        assert lines[0].startswith("AGENTS.md: VALID")
        assert str(path) in lines[0]
        assert str(path.stat().st_size) in lines[0]

    def test_russian_section_aliases_accepted(self, tmp_path):
        """RU headings must satisfy the English section requirements."""

        text = (
            "# AGENTS.md\n\n"
            "## Обзор проекта\n\nПроект делает полезное.\n\n"
            "## Установка\n\nШаги окружения здесь.\n\n"
            "## Архитектура\n\nСлои и дерево.\n\n"
            "## Тестирование\n\nПрогон pytest.\n\n"
            "## Запуск\n\nЕщё раздел для объёма, чтобы не был слишком коротким.\n"
        )
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is True, result.errors

    def test_atx_headings_with_more_hashes(self, tmp_path):
        text = _VALID_BODY.replace("## Project Overview", "### Project Overview")
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is True


class TestMissingAndUnreadable:
    def test_missing_file(self, tmp_path):
        result = v.validate_agents_md(tmp_path / "nope.md")
        assert result.valid is False
        assert any("not found" in e for e in result.errors)
        assert result.size_bytes == 0

    def test_directory_instead_of_file(self, tmp_path):
        result = v.validate_agents_md(tmp_path)
        assert result.valid is False

    def test_not_utf8(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_bytes(b"# AGENTS.md\n\n\xff\xfe binary garbage\n")
        result = v.validate_agents_md(path)
        assert result.valid is False
        assert any("UTF-8" in e for e in result.errors)


class TestSizeLimits:
    def test_too_large_is_error(self, tmp_path):
        filler = "## Project Overview\n\n" + ("слово " * 4000) + "\n"
        text = (
            "# AGENTS.md\n\n## Setup\n\nУстановка.\n\n## Architecture\n\nДерево.\n\n"
            f"## Testing\n\nТесты.\n\n{filler}"
        )
        assert len(text.encode("utf-8")) > v.MAX_BYTES
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is False
        assert any("32 KiB" in e for e in result.errors)

    def test_short_file_warns_but_valid(self, tmp_path):
        text = (
            "# AGENTS.md\n\n## Project Overview\n\nОбзор.\n\n"
            "## Setup\n\nУстановка.\n\n## Architecture\n\nДерево.\n\n## Testing\n\nТесты.\n"
        )
        assert len(text.encode("utf-8")) < v.MIN_BYTES
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is True
        assert len(result.warnings) == 1
        assert "короткий" in result.warnings[0]
        assert any("?" == line.strip()[0] for line in result.summary_lines()[1:])


class TestMissingSections:
    def test_each_missing_section_reported(self, tmp_path):
        text = (
            "# AGENTS.md\n\n" + "Достаточно длинный текст, но без нужных разделов. " * 10
        )
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is False
        assert len(result.errors) == len(v.REQUIRED_SECTIONS)
        for required in v.REQUIRED_SECTIONS:
            assert f"нет раздела: {required}" in result.errors

    def test_one_section_missing(self, tmp_path):
        text = _VALID_BODY.replace("## Testing\n\nКак запускать тесты.\n\n", "")
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is False
        assert result.errors == ["нет раздела: Testing"]

    def test_section_prefix_counts(self, tmp_path):
        """`## Setup steps` satisfies `Setup` (prefix match)."""

        text = _VALID_BODY.replace("## Setup", "## Setup steps")
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is True

    def test_no_headings_at_all(self, tmp_path):
        text = "Просто текст без заголовков, но достаточно длинный. " * 8
        result = v.validate_agents_md(_write(tmp_path, "AGENTS.md", text))
        assert result.valid is False
        assert len(result.errors) == len(v.REQUIRED_SECTIONS)


class TestHelpers:
    def test_default_agents_path(self, tmp_path):
        assert v.default_agents_path(tmp_path) == tmp_path / "AGENTS.md"

    def test_accepts_str_path(self, tmp_path):
        result = v.validate_agents_md(str(tmp_path / "nope.md"))
        assert result.valid is False

    def test_summary_lines_invalid_prefix(self, tmp_path):
        result = v.validate_agents_md(tmp_path / "nope.md")
        assert result.summary_lines()[0].startswith("AGENTS.md: INVALID")
