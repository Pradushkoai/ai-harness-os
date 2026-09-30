"""Router configuration: YAML file with env-var expansion + builtin defaults.

Config discovery order:
    1. $RLP_CONFIG (explicit path)
    2. ./rlp.config.yaml (project-local)
    3. builtin defaults (see DEFAULT_TASKS)

Security rule: YAML never contains secrets. Providers reference env var
*names* (`api_key_env: DEEPSEEK_API_KEY`); `${VAR}` expansion works for
non-secret fields (base_url etc.) — keys are resolved at adapter build time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from ..types import ConfigError, ModelRef

CONFIG_FILENAME = "rlp.config.yaml"

# Builtin routing: task -> ordered fallback chain of "provider/model".
# The judge chain intentionally differs from coding chains: an LLM judging
# code should not be the same model that wrote it.
DEFAULT_TASKS: dict[str, list[str]] = {
    "coding": [
        "deepseek/deepseek-chat",
        "zai/glm-4.6",
        "gigachat/GigaChat-Pro",
    ],
    "reasoning": [
        "deepseek/deepseek-reasoner",
        "zai/glm-4.6",
    ],
    "cheap": [
        "zai/glm-4.5-air",
        "deepseek/deepseek-chat",
        "gigachat/GigaChat-Lite",
    ],
    "judge": [
        "zai/glm-4.6",
        "deepseek/deepseek-chat",
    ],
}

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "coding": {"temperature": 0.2},
    "reasoning": {},
    "cheap": {"temperature": 0.3},
    "judge": {"temperature": 0.0},
}

_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} / ${VAR:-default} in strings."""

    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            var, default = match.group(1), match.group(2)
            env_value = os.environ.get(var)
            if env_value is not None and env_value != "":
                return env_value
            return default if default is not None else ""

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class RouterConfig:
    """Parsed routing configuration."""

    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_TASKS))
    params: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    defaults: dict[str, Any] = field(
        default_factory=lambda: {"task": "coding", "retries": 2, "timeout_s": 60.0}
    )
    source: str = "builtin"

    # -- accessors -----------------------------------------------------------

    @property
    def default_task(self) -> str:
        return str(self.defaults.get("task", "coding"))

    @property
    def retries(self) -> int:
        return int(self.defaults.get("retries", 2))

    @property
    def timeout_s(self) -> float:
        return float(self.defaults.get("timeout_s", 60.0))

    def chain_for(self, task: Optional[str]) -> list[ModelRef]:
        task_name = task or self.default_task
        if task_name not in self.tasks:
            known = ", ".join(sorted(self.tasks))
            raise ConfigError(
                f"unknown task '{task_name}' (known tasks: {known})"
            )
        return [ModelRef.parse(ref) for ref in self.tasks[task_name]]

    def provider_names(self) -> list[str]:
        """Union of known presets and custom providers declared in config."""

        from ..providers.registry import PRESETS

        names = dict.fromkeys(list(PRESETS) + list(self.providers))
        return list(names)

    # -- loading -------------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], source: str = "mapping") -> "RouterConfig":
        data = dict(data)
        providers = _expand_env(data.get("providers") or {})
        tasks_raw = _expand_env(data.get("routing", {}).get("tasks") or {})
        params = _expand_env(data.get("routing", {}).get("params") or {})
        defaults = _expand_env(data.get("defaults") or {})

        tasks: dict[str, list[str]] = dict(DEFAULT_TASKS)
        for task_name, chain in tasks_raw.items():
            if not isinstance(chain, list) or not all(isinstance(x, str) for x in chain):
                raise ConfigError(
                    f"task '{task_name}' chain must be a list of 'provider/model' strings"
                )
            # validate early, fail loudly on typos
            refs = [ModelRef.parse(x) for x in chain]
            tasks[str(task_name)] = [str(r) for r in refs]

        merged_params = dict(DEFAULT_PARAMS)
        merged_params.update({str(k): dict(v) for k, v in params.items()})

        merged_defaults = {"task": "coding", "retries": 2, "timeout_s": 60.0}
        merged_defaults.update(defaults)

        return cls(
            providers={str(k): dict(v) for k, v in providers.items()},
            tasks=tasks,
            params=merged_params,
            defaults=merged_defaults,
            source=source,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RouterConfig":
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config root must be a mapping: {path}")
        return cls.from_mapping(data, source=str(path))

    @classmethod
    def builtin(cls) -> "RouterConfig":
        return cls()


def find_config() -> Optional[Path]:
    """Resolve the config file path without loading it."""

    explicit = os.environ.get("RLP_CONFIG")
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"RLP_CONFIG points to a missing file: {path}")
        return path
    local = Path.cwd() / CONFIG_FILENAME
    if local.is_file():
        return local
    return None


def load_config() -> tuple[RouterConfig, Optional[Path]]:
    """Load config from discovery path or fall back to builtin defaults."""

    path = find_config()
    if path is None:
        return RouterConfig.builtin(), None
    return RouterConfig.from_yaml(path), path
