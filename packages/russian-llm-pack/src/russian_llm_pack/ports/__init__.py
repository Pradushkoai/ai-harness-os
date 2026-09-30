"""LLM port — the ONE interface the harness depends on.

Everything else in this package is an implementation detail behind this port.
When GLM-6 or GigaChat-3 ships, or when a provider dies overnight, only
adapters and config change — harness code never does.
"""

from .llm_port import LLMPort

__all__ = ["LLMPort"]
