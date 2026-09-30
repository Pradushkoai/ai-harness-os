"""Unit tests for core types."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from russian_llm_pack.types import (
    ChatMessage,
    ConfigError,
    ModelRef,
    UsageInfo,
    coerce_messages,
)


class TestChatMessage:
    def test_to_dict(self):
        assert ChatMessage.user("hi").to_dict() == {"role": "user", "content": "hi"}

    def test_constructors(self):
        assert ChatMessage.system("s").role == "system"
        assert ChatMessage.assistant("a").role == "assistant"


class TestCoerceMessages:
    def test_single_string(self):
        msgs = coerce_messages("hello")
        assert msgs == [ChatMessage.user("hello")]

    def test_single_message(self):
        msg = ChatMessage.system("sys")
        assert coerce_messages(msg) == [msg]

    def test_mixed_list(self):
        msgs = coerce_messages(["a", ChatMessage.user("b"), {"role": "assistant", "content": "c"}])
        assert [m.role for m in msgs] == ["user", "user", "assistant"]
        assert [m.content for m in msgs] == ["a", "b", "c"]

    def test_bad_type_raises(self):
        with pytest.raises(TypeError):
            coerce_messages([42])


class TestUsageInfo:
    def test_from_none(self):
        assert UsageInfo.from_openai(None).total_tokens == 0

    def test_from_openai_usage(self):
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        info = UsageInfo.from_openai(usage)
        assert (info.input_tokens, info.output_tokens, info.total_tokens) == (10, 20, 30)


class TestModelRef:
    def test_parse(self):
        ref = ModelRef.parse("deepseek/deepseek-chat")
        assert ref.provider == "deepseek"
        assert ref.model == "deepseek-chat"

    @pytest.mark.parametrize("bad", ["noslash", "deepseek/", "/model", " "])
    def test_parse_invalid(self, bad):
        with pytest.raises(ConfigError):
            ModelRef.parse(bad)

    def test_str(self):
        assert str(ModelRef.parse("zai/glm-4.6")) == "zai/glm-4.6"
