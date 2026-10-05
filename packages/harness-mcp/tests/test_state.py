"""SessionState tests: TTL, size cap, ids (roadmap B3)."""

from __future__ import annotations

import pytest

from harness_mcp.state import SessionState


class TestBasics:
    def test_put_then_get(self):
        clock = {"t": 0.0}
        state = SessionState(now=lambda: clock["t"])
        state.put("run-1", {"payload": 42})
        assert state.get("run-1") == {"payload": 42}

    def test_unknown_id_is_none(self):
        state = SessionState()
        assert state.get("run-nope") is None

    def test_new_id_is_opaque_and_prefixed(self):
        state = SessionState()
        run_id = state.new_id("run")
        assert run_id.startswith("run-")
        assert len(run_id) > len("run-")

    def test_overwrite_keeps_one_entry(self):
        state = SessionState()
        state.put("run-1", "a")
        state.put("run-1", "b")
        assert state.get("run-1") == "b"
        assert len(state) == 1


class TestTtl:
    def test_expired_entry_is_evicted(self):
        clock = {"t": 0.0}
        state = SessionState(ttl_seconds=100.0, now=lambda: clock["t"])
        state.put("run-1", "data")
        clock["t"] = 99.0
        assert state.get("run-1") == "data"
        clock["t"] = 101.0
        assert state.get("run-1") is None

    def test_len_respects_ttl(self):
        clock = {"t": 0.0}
        state = SessionState(ttl_seconds=10.0, now=lambda: clock["t"])
        state.put("a", 1)
        state.put("b", 2)
        clock["t"] = 20.0
        assert len(state) == 0


class TestSizeCap:
    def test_oldest_entry_evicted_on_overflow(self):
        clock = {"t": 0.0}
        state = SessionState(max_entries=2, now=lambda: clock["t"])
        state.put("a", 1)
        clock["t"] = 1.0
        state.put("b", 2)
        clock["t"] = 2.0
        state.put("c", 3)
        assert state.get("a") is None
        assert state.get("b") == 2
        assert state.get("c") == 3
        assert len(state) == 2


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs", [{"ttl_seconds": 0}, {"ttl_seconds": -5}, {"max_entries": 0}]
    )
    def test_bad_config_rejected(self, kwargs):
        with pytest.raises(ValueError):
            SessionState(**kwargs)
