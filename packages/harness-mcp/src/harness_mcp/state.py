"""Session state for harness-mcp (roadmap 2.1, step B3).

The MCP server is a long-lived process, an agent chats with it over many
tools/call turns — so `run_loop` and `eval_summary` park their heavy
results in memory and hand back a short run_id. The agent re-asks for
details without re-running the loop: one generation, many questions.

Deliberate v0.1 limits (documented in the roadmap):
    - memory only, no persistence across restarts;
    - TTL eviction (default 15 minutes) + a hard entry cap, so a chatty
      client cannot grow the server without bound;
    - monotonic clock, immune to wall-clock jumps.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable, Optional


class SessionState:
    """In-memory run cache with TTL and a size cap."""

    def __init__(
        self,
        ttl_seconds: float = 900.0,
        max_entries: int = 64,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._now = now
        self._entries: dict[str, tuple[float, object]] = {}

    def new_id(self, prefix: str = "run") -> str:
        """Fresh opaque id (uuid4 hex, prefix-colonated)."""

        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def put(self, run_id: str, value: object) -> None:
        """Store `value` under `run_id` (caller-chosen or new_id())."""

        self._purge()
        if len(self._entries) >= self._max_entries and run_id not in self._entries:
            oldest = min(self._entries, key=lambda k: self._entries[k][0])
            del self._entries[oldest]
        self._entries[run_id] = (self._now(), value)

    def get(self, run_id: str) -> Optional[object]:
        """Return the cached value or None (expired / unknown / evicted)."""

        self._purge()
        entry = self._entries.get(run_id)
        if entry is None:
            return None
        return entry[1]

    def __len__(self) -> int:
        self._purge()
        return len(self._entries)

    def _purge(self) -> None:
        deadline = self._now() - self._ttl
        stale = [k for k, (ts, _) in self._entries.items() if ts < deadline]
        for key in stale:
            del self._entries[key]
