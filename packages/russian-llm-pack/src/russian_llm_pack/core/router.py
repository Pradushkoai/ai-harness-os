"""Router: task -> ordered fallback chain, with retry policy and events.

Fallback semantics per chain entry `provider/model`:
    - provider not built (no API key / unknown / native-stub) -> skip entry
    - ProviderAuthError    -> no retry, next entry (key is wrong, retry won't help)
    - ProviderTransient    -> retry up to `retries` times, then next entry
    - ProviderRequestError -> no retry, next entry (4xx / bad model name)

When the whole chain is exhausted -> NoAvailableModelError carrying the
collected error list (observability).

Events: an optional `on_event(dict)` callback receives every routing decision
(skip / retry / error / success). The CLI prints them as JSONL with --verbose;
later the harness will forward them to Langfuse — the hook is the seam.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..providers.registry import build_provider, preset_info
from ..types import (
    CompletionResult,
    ModelRef,
    NoAvailableModelError,
    ProviderAuthError,
    ProviderError,
    ProviderTransientError,
)
from .config import RouterConfig

EventCallback = Callable[[dict], None]


class Router:
    """Routes task-oriented completion requests across providers."""

    def __init__(
        self,
        config: RouterConfig,
        providers: Optional[dict[str, Any]] = None,
        on_event: Optional[EventCallback] = None,
    ) -> None:
        self._config = config
        self._providers: dict[str, Any] = dict(providers or {})
        self._on_event = on_event

    # -- construction --------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: RouterConfig,
        on_event: Optional[EventCallback] = None,
        clients: Optional[dict[str, Any]] = None,
    ) -> "Router":
        """Build a Router, instantiating every provider that has a key.

        Providers without keys are silently absent — chains simply skip them.
        `clients` maps provider name -> injected client (for tests).
        """

        clients = clients or {}
        providers: dict[str, Any] = {}
        for name in config.provider_names():
            adapter = build_provider(
                name,
                config.providers.get(name),
                client=clients.get(name),
                timeout=config.timeout_s,
            )
            if adapter is not None:
                providers[name] = adapter
        return cls(config=config, providers=providers, on_event=on_event)

    # -- introspection ---------------------------------------------------------

    @property
    def config(self) -> RouterConfig:
        return self._config

    @property
    def providers(self) -> dict[str, Any]:
        return dict(self._providers)

    def status(self) -> list[dict[str, Any]]:
        """Per-provider status for `rlp check` (includes keyless providers)."""

        out = []
        for name in self._config.provider_names():
            info = preset_info(name, self._config.providers.get(name))
            info["built"] = name in self._providers
            out.append(info)
        return out

    def resolve(self, task: Optional[str] = None) -> list[ModelRef]:
        return self._config.chain_for(task)

    # -- main API ----------------------------------------------------------------

    def complete(
        self,
        task: Optional[str],
        messages: Any,
        *,
        model: Optional[str] = None,
        **kwargs,
    ) -> CompletionResult:
        """Run a completion through the task's fallback chain.

        `model` ("provider/model") bypasses routing and calls one model
        directly — used by `rlp chat --model ...` for debugging.
        """

        task_name = task or self._config.default_task

        if model is not None:
            ref = ModelRef.parse(model)
            provider = self._providers.get(ref.provider)
            if provider is None:
                raise NoAvailableModelError(
                    f"{ref.provider}:{ref.model}", [str(ref)],
                    [f"provider '{ref.provider}' is not configured (no API key?)"],
                )
            return self._call(provider, ref, task_name, messages, kwargs)

        chain = self.resolve(task_name)
        errors: list[str] = []
        for ref in chain:
            provider = self._providers.get(ref.provider)
            if provider is None:
                self._emit({"event": "skip", "task": task_name, "ref": str(ref),
                            "reason": "provider not configured"})
                errors.append(f"{ref}: provider not configured")
                continue

            params = dict(self._config.params.get(task_name, {}))
            params.update(kwargs)

            attempts = self._config.retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    result = provider.complete(messages, model=ref.model, **params)
                except ProviderAuthError as exc:
                    self._emit({"event": "error", "task": task_name, "ref": str(ref),
                                "attempt": attempt, "error": str(exc), "kind": "auth"})
                    errors.append(str(exc))
                    break  # wrong key: retrying is pointless
                except ProviderTransientError as exc:
                    self._emit({"event": "retry" if attempt < attempts else "error",
                                "task": task_name, "ref": str(ref),
                                "attempt": attempt, "error": str(exc), "kind": "transient"})
                    errors.append(str(exc))
                    if attempt < attempts:
                        continue
                    break  # chain entry exhausted -> next model
                except ProviderError as exc:
                    self._emit({"event": "error", "task": task_name, "ref": str(ref),
                                "attempt": attempt, "error": str(exc), "kind": "request"})
                    errors.append(str(exc))
                    break

                self._emit({
                    "event": "ok", "task": task_name, "ref": str(ref),
                    "attempt": attempt,
                    "latency_ms": round(result.latency_ms, 1),
                    "usage": {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "total_tokens": result.usage.total_tokens,
                    },
                })
                return result

        raise NoAvailableModelError(task_name, [str(r) for r in chain], errors)

    def stream(self, task: Optional[str], messages: Any, **kwargs):
        """Stream a completion.

        v0.1 semantics: pick the FIRST available model in the chain — no
        mid-stream fallback (deltas already delivered cannot be taken back).
        If the first available model fails at connect time, the next chain
        entry is tried. Mid-stream failures raise ProviderError.
        """

        task_name = task or self._config.default_task
        chain = self.resolve(task_name)
        params = dict(self._config.params.get(task_name, {}))
        params.update(kwargs)

        errors: list[str] = []
        for ref in chain:
            provider = self._providers.get(ref.provider)
            if provider is None:
                errors.append(f"{ref}: provider not configured")
                continue
            try:
                iterator = provider.stream(messages, model=ref.model, **params)
                first = next(iterator, None)  # force the connection open
            except ProviderError as exc:
                errors.append(str(exc))
                self._emit({"event": "error", "task": task_name, "ref": str(ref),
                            "error": str(exc)})
                continue
            self._emit({"event": "ok", "task": task_name, "ref": str(ref), "mode": "stream"})
            if first is not None:
                yield first
            yield from iterator
            return

        raise NoAvailableModelError(task_name, [str(r) for r in chain], errors)

    # -- internals ----------------------------------------------------------------

    def _call(
        self,
        provider: Any,
        ref: ModelRef,
        task_name: str,
        messages: Any,
        kwargs: dict,
    ) -> CompletionResult:
        try:
            result = provider.complete(messages, model=ref.model, **kwargs)
        except ProviderError as exc:
            raise NoAvailableModelError(
                f"{task_name}:{ref}", [str(ref)], [str(exc)]
            ) from exc
        self._emit({
            "event": "ok", "task": task_name, "ref": str(ref), "mode": "direct",
            "latency_ms": round(result.latency_ms, 1),
        })
        return result

    def _emit(self, event: dict) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 — events must never break routing
                pass
