"""Tests for extract_bsl_code: LLM response -> BSL module text."""

from __future__ import annotations

from harness_loop.extract import extract_bsl_code

from conftest import MODULE_BROKEN, MODULE_OK, fenced


class TestFenced:
    def test_bsl_fence_with_prose_around(self):
        assert extract_bsl_code(fenced(MODULE_OK)) == MODULE_OK

    def test_untagged_fence(self):
        assert extract_bsl_code(f"```\n{MODULE_OK.rstrip()}\n```") == MODULE_OK

    def test_wrong_language_tag_but_bsl_content(self):
        """Model tagged the block `python` but the content is BSL — accept it."""

        assert extract_bsl_code(fenced(MODULE_OK, lang="python")) == MODULE_OK

    def test_last_fence_wins(self):
        """Draft first, corrected code last — the final version is what we want."""

        text = fenced(MODULE_BROKEN) + "\n" + fenced(MODULE_OK)
        assert extract_bsl_code(text) == MODULE_OK

    def test_os_tag(self):
        assert extract_bsl_code(fenced(MODULE_OK, lang="os")) == MODULE_OK

    def test_case_insensitive_tag(self):
        assert extract_bsl_code(fenced(MODULE_OK, lang="BSL")) == MODULE_OK

    def test_empty_fence_is_skipped(self):
        text = "```bsl\n```\n" + fenced(MODULE_OK)
        assert extract_bsl_code(text) == MODULE_OK


class TestNoFence:
    def test_plain_bsl_text(self):
        assert extract_bsl_code(MODULE_OK) == MODULE_OK

    def test_directive_annotation_start(self):
        code = "&Вместо(\"ОбщийМодуль.Метод\")\nПроцедура Моё()\nКонецПроцедуры\n"
        assert extract_bsl_code(code) == code

    def test_preprocessor_start(self):
        code = "#Область МояОбласть\nПроцедура Моё()\nКонецПроцедуры\n#КонецОбласти\n"
        assert extract_bsl_code(code) == code

    def test_prose_only_returns_none(self):
        assert extract_bsl_code("Извини, я не могу написать этот модуль.") is None

    def test_empty_returns_none(self):
        assert extract_bsl_code("") is None
        assert extract_bsl_code(None) is None
        assert extract_bsl_code("   \n\t ") is None

    def test_python_code_without_bsl_look_returns_none(self):
        assert extract_bsl_code("def hello():\n    return 42\n") is None


class TestNormalization:
    def test_single_trailing_newline(self):
        text = fenced(MODULE_OK) + "\n\n\n"
        assert extract_bsl_code(text) == MODULE_OK

    def test_crlf_fences(self):
        text = f"```bsl\r\n{MODULE_OK.rstrip()}\r\n```"
        assert extract_bsl_code(text) == MODULE_OK
