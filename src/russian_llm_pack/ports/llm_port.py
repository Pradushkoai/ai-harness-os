"""LLMPort protocol.

The stable seam between the harness and the mutable world of LLM providers.

Design notes:
- Sync-first for v0.1. An async mirror (`acomplete` / `astream`) is planned
  for v0.2 once the harness (LangGraph) actually consumes it.
- `model` is optional: the adapter has a default, but the router always
  passes an explicit model from the fallback chain.
- `**kwargs` are passed through to the provider (temperature, max_tokens,
  top_p, ...). Keep them provider-neutral: OpenAI-compatible names.
"""

from __future__ import annotations

from typing import Iterator, List, Protocol, runtime_checkable

from ..types import ChatMessage, CompletionResult, StreamEvent


@runtime_checkable
class LLMPort(Protocol):
    """Stable interface every provider adapter must satisfy."""

    name: str

    def complete(
        self,
        messages: List[ChatMessage],
        *,
        model: str | None = None,
        **kwargs,
    ) -> CompletionResult:
        """Run a chat completion and return the mapped result."""
        ...

    def stream(
        self,
        messages: List[ChatMessage],
        *,
        model: str | None = None,
        **kwargs,
    ) -> Iterator[StreamEvent]:
        """Stream a chat completion as delta events."""
        ...
