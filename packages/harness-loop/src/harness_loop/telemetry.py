"""Optional Langfuse telemetry over the two existing seams.

    Router(on_event=...)          -> on_router_event  (routing decisions)
    loop.run(on_iteration=...)    -> on_iteration      (per-iteration records)

Design rules:
    - ZERO new dependencies: plain urllib POST to the Langfuse ingestion
      API (`POST /api/public/ingestion`, basic auth pk:sk). The official
      SDK can appear later as an optional backend — the payload is already
      shaped as `generation-create` events, so switching is trivial.
    - Secrets only via env *names* (repo rule): the constructor takes the
      NAMES of the env variables, values are read at send time.
    - No keys -> silent no-op (the flag is optional, nothing breaks).
    - Any network error is swallowed: telemetry must never break the loop.
    - Payloads carry no code and no prompts — only metrics, model names,
      diagnostic counts and capped diagnostic lines (LS output, not PII).

Env contract:
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY — cloud or self-host keys
    LANGFUSE_HOST — override for self-hosted (default: cloud.langfuse.com)
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen

PUBLIC_KEY_ENV = "LANGFUSE_PUBLIC_KEY"
SECRET_KEY_ENV = "LANGFUSE_SECRET_KEY"
HOST_ENV = "LANGFUSE_HOST"
DEFAULT_HOST = "https://cloud.langfuse.com"

_MAX_DIAG_METADATA = 20  # diagnostic lines embedded into metadata, capped


def _http_post(url: str, headers: dict, body: bytes, timeout: float) -> None:
    """Module-level transport seam (monkeypatched in tests; stdlib urllib)."""

    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 — https by default
        response.read()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LangfuseTelemetry:
    """Buffering adapter: events/iterations -> Langfuse generation records.

    Usage:
        telemetry = LangfuseTelemetry(trace_id="run-42")   # id groups everything
        router = Router.from_config(rc, on_event=telemetry.on_router_event)
        loop.run(task, on_iteration=telemetry.on_iteration)
        telemetry.flush()                                   # one batched POST
    """

    def __init__(
        self,
        *,
        public_key_env: str = PUBLIC_KEY_ENV,
        secret_key_env: str = SECRET_KEY_ENV,
        host_env: str = HOST_ENV,
        host_default: str = DEFAULT_HOST,
        trace_id: Optional[str] = None,
        trace_name: str = "harness-loop",
        flush_every: int = 25,
        timeout_s: float = 5.0,
    ) -> None:
        self._public_key_env = public_key_env
        self._secret_key_env = secret_key_env
        self._host_env = host_env
        self._host_default = host_default
        self._trace_id = trace_id or str(uuid.uuid4())
        self._trace_name = trace_name
        self._flush_every = max(1, flush_every)
        self._timeout_s = timeout_s
        self._buffer: list[dict] = []
        self.sent_events = 0
        self.send_errors = 0

    # -- configuration -------------------------------------------------------

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def enabled(self) -> bool:
        """True when both keys are present in the environment."""

        return bool(os.environ.get(self._public_key_env)) and bool(
            os.environ.get(self._secret_key_env)
        )

    @property
    def host(self) -> str:
        return os.environ.get(self._host_env) or self._host_default

    # -- seams ----------------------------------------------------------------

    def on_router_event(self, event: dict) -> None:
        """Router on_event seam: skip/retry/error/ok routing decisions."""

        if not self.enabled:
            return
        record = {
            "type": "generation-create",
            "id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "traceId": self._trace_id,
            "name": f"rlp:{event.get('event', '?')}:{event.get('ref', '?')}",
            "metadata": {"kind": "router", **_stringify(event)},
        }
        kind = str(event.get("event", ""))
        if kind == "ok":
            usage = event.get("usage") or {}
            record["usage"] = {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
            record["model"] = str(event.get("ref", ""))
            record["latency_ms"] = event.get("latency_ms")
        if kind == "error":
            record["level"] = "ERROR"
            record["statusMessage"] = str(event.get("error", ""))[:500]
        elif kind == "skip":
            record["level"] = "DEBUG"
        self._buffer.append(record)
        self._maybe_autoflush()

    def on_iteration(self, log) -> None:
        """Loop on_iteration seam: one generation record per iteration."""

        if not self.enabled:
            return
        metadata = {
            "kind": "iteration",
            "code_extracted": bool(log.code_extracted),
            "verified": log.verified,
            "errors": log.errors,
            "warnings": log.warnings,
            "informations": log.informations,
            "verify_ms": round(log.verify_ms, 1),
            "judge_verdict": log.judge_verdict,
            "judge_issues_count": len(log.judge_issues),
            "judge_ms": round(log.judge_ms, 1),
            "note": (log.note or "")[:200],
        }
        if log.diagnostics:
            metadata["diagnostics"] = [
                str(d)[:300] for d in log.diagnostics[:_MAX_DIAG_METADATA]
            ]
        record = {
            "type": "generation-create",
            "id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "traceId": self._trace_id,
            "name": f"{self._trace_name}:iteration-{log.index}",
            "model": log.model or "unknown",
            "usage": {
                "input": log.prompt_tokens,
                "output": log.completion_tokens,
                "total": log.prompt_tokens + log.completion_tokens,
            },
            "latency_ms": round(log.llm_latency_ms, 1),
            "metadata": metadata,
        }
        if log.verified is False:
            record["level"] = "WARNING"
        if log.judge_verdict is False:
            record["statusMessage"] = "judge rejected the module"
        self._buffer.append(record)
        self._maybe_autoflush()

    # -- delivery ----------------------------------------------------------------

    def flush(self) -> bool:
        """Send the buffered records as one ingestion batch; never raises."""

        if not self._buffer or not self.enabled:
            return False
        payload = {
            "batchId": str(uuid.uuid4()),
            "batch": list(self._buffer),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        public = os.environ.get(self._public_key_env, "")
        secret = os.environ.get(self._secret_key_env, "")
        auth = base64.b64encode(f"{public}:{secret}".encode("utf-8")).decode("ascii")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        }
        url = f"{self.host.rstrip('/')}/api/public/ingestion"
        try:
            _http_post(url, headers, body, self._timeout_s)
        except Exception:  # noqa: BLE001 — telemetry must never break the loop
            self.send_errors += 1
            return False
        self.sent_events += len(self._buffer)
        self._buffer.clear()
        return True

    def pending(self) -> int:
        return len(self._buffer)

    # -- internals ----------------------------------------------------------------

    def _maybe_autoflush(self) -> None:
        if len(self._buffer) >= self._flush_every:
            self.flush()


def _stringify(event: dict) -> dict:
    """JSON-safe copy of a router event (drop nothing, coerce values)."""

    safe: dict = {}
    for key, value in event.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = json.dumps(value, ensure_ascii=False, default=str)
    return safe


def telemetry_from_env(trace_id: Optional[str] = None) -> Optional[LangfuseTelemetry]:
    """Factory for CLI wiring: None (unset) or a configured adapter.

    The returned adapter is always constructed — enabled is checked lazily
    per event, so a missing key degrades to a no-op without raising here.
    """

    return LangfuseTelemetry(trace_id=trace_id)
