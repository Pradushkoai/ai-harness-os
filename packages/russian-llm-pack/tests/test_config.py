"""Unit tests for config loading, env expansion and validation."""

from __future__ import annotations

import textwrap

import pytest

from russian_llm_pack.core.config import RouterConfig, load_config
from russian_llm_pack.types import ConfigError


class TestFromMapping:
    def test_defaults(self):
        config = RouterConfig.from_mapping({})
        assert config.tasks["coding"][0] == "deepseek/deepseek-chat"
        assert config.default_task == "coding"
        assert config.retries == 2

    def test_custom_tasks_override(self):
        config = RouterConfig.from_mapping({
            "routing": {"tasks": {"coding": ["zai/glm-4.6"]}}
        })
        assert config.tasks["coding"] == ["zai/glm-4.6"]
        # other builtin tasks survive
        assert "judge" in config.tasks

    def test_provider_overrides(self):
        config = RouterConfig.from_mapping({
            "providers": {"zai": {"api_key_env": "MY_CUSTOM_ENV"}}
        })
        assert config.providers["zai"]["api_key_env"] == "MY_CUSTOM_ENV"

    def test_invalid_chain_type_raises(self):
        with pytest.raises(ConfigError):
            RouterConfig.from_mapping({"routing": {"tasks": {"coding": "deepseek/deepseek-chat"}}})

    def test_invalid_model_ref_raises(self):
        with pytest.raises(ConfigError):
            RouterConfig.from_mapping({"routing": {"tasks": {"coding": ["no-slash"]}}})

    def test_defaults_merge(self):
        config = RouterConfig.from_mapping({"defaults": {"retries": 5}})
        assert config.retries == 5
        assert config.default_task == "coding"


class TestEnvExpansion:
    def test_env_var_expansion(self, monkeypatch):
        monkeypatch.setenv("RLP_TEST_URL", "https://example.internal")
        config = RouterConfig.from_mapping({
            "providers": {"zai": {"base_url": "${RLP_TEST_URL}/v4"}}
        })
        assert config.providers["zai"]["base_url"] == "https://example.internal/v4"

    def test_env_var_with_default(self, monkeypatch):
        monkeypatch.delenv("RLP_MISSING_VAR", raising=False)
        config = RouterConfig.from_mapping({
            "providers": {"zai": {"base_url": "${RLP_MISSING_VAR:-https://fallback}"}}
        })
        assert config.providers["zai"]["base_url"] == "https://fallback"

    def test_empty_env_falls_to_default(self, monkeypatch):
        monkeypatch.setenv("RLP_EMPTY_VAR", "")
        config = RouterConfig.from_mapping({
            "providers": {"zai": {"base_url": "${RLP_EMPTY_VAR:-https://fallback}"}}
        })
        assert config.providers["zai"]["base_url"] == "https://fallback"


class TestLoadConfig:
    def test_yaml_roundtrip(self, tmp_path, monkeypatch):
        yaml_text = textwrap.dedent("""
            providers:
              deepseek:
                base_url: https://api.deepseek.com
            routing:
              tasks:
                coding:
                  - deepseek/deepseek-chat
                  - zai/glm-4.6
            defaults:
              retries: 1
              task: coding
        """)
        path = tmp_path / "rlp.config.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        monkeypatch.setenv("RLP_CONFIG", str(path))

        config, loaded_path = load_config()
        assert loaded_path == path
        assert config.tasks["coding"] == ["deepseek/deepseek-chat", "zai/glm-4.6"]
        assert config.retries == 1

    def test_missing_explicit_config_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RLP_CONFIG", str(tmp_path / "nope.yaml"))
        with pytest.raises(ConfigError):
            load_config()

    def test_falls_back_to_builtin(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RLP_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # no rlp.config.yaml here
        config, path = load_config()
        assert path is None
        assert config.source == "builtin"

    def test_invalid_yaml_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "rlp.config.yaml"
        path.write_text("routing: [unclosed", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError):
            load_config()


class TestChainFor:
    def test_known_task(self):
        refs = RouterConfig.builtin().chain_for("judge")
        assert [str(r) for r in refs] == RouterConfig().tasks["judge"]

    def test_unknown_task_raises(self):
        with pytest.raises(ConfigError):
            RouterConfig.builtin().chain_for("nope")

    def test_none_task_uses_default(self):
        refs = RouterConfig.builtin().chain_for(None)
        assert [str(r) for r in refs] == RouterConfig().tasks["coding"]

    def test_judge_chain_is_independent_of_coding(self):
        """Contract: the judge must not default to the coding provider —
        an LLM reviewing code should be a different model than the one that
        wrote it. Qwen leads the judge chain, deepseek leads coding."""
        judge = [str(r) for r in RouterConfig.builtin().chain_for("judge")]
        coding = [str(r) for r in RouterConfig.builtin().chain_for("coding")]
        assert judge[0] == "qwen/qwen-max"
        assert coding[0] == "deepseek/deepseek-chat"
        assert judge[0].split("/")[0] != coding[0].split("/")[0]

    def test_every_builtin_chain_model_has_a_preset(self):
        """Every 'provider/model' in DEFAULT_TASKS must resolve to a known
        preset — otherwise Router.from_config would silently skip it."""
        from russian_llm_pack.providers.registry import PRESETS

        for task, chain in RouterConfig.builtin().tasks.items():
            for ref in chain:
                provider = ref.split("/")[0]
                assert provider in PRESETS, f"{task}: unknown provider {provider!r}"
