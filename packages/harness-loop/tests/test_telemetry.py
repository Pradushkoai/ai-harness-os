"""Langfuse telemetry tests: no-op, payloads, flushing, error swallowing.

No network: the module-level `_http_post` transport is monkeypatched with
a recorder, env keys are set/del via monkeypatch.
"""

from __future__ import annotations

import base64
import json

import pytest

from harness_loop import telemetry as telemetry_module
from harness_loop.telemetry import LangfuseTelemetry, telemetry_from_env
from harness_loop.types import IterationLog



class Recorder:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body.decode("utf-8")),
                "timeout": timeout,
            }
        )


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(telemetry_module, "_http_post", rec)
    return rec


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    return ("pk-test-123", "sk-test-456")


def make_log(**overrides) -> IterationLog:
    defaults = dict(
        index=1,
        model="deepseek/deepseek-chat",
        code_extracted=True,
        verified=False,
        errors=2,
        warnings=1,
        prompt_tokens=100,
        completion_tokens=50,
        llm_latency_ms=500.0,
        verify_ms=250.0,
        diagnostics=["module.bsl:4:1: ParseError: сломано"],
        judge_verdict=False,
        judge_issues=["пустой массив"],
        judge_ms=300.0,
    )
    defaults.update(overrides)
    return IterationLog(**defaults)


class TestDisabled:
    def test_no_keys_is_silent_noop(self, recorder):
        telemetry = LangfuseTelemetry()  # no env keys in this test
        telemetry.on_router_event({"event": "ok", "ref": "deepseek/x", "task": "coding"})
        telemetry.on_iteration(make_log())

        assert telemetry.enabled is False
        assert telemetry.pending() == 0
        assert telemetry.flush() is False
        assert recorder.calls == []


class TestRouterEvents:
    def test_ok_event_maps_to_generation(self, recorder, keys):
        telemetry = LangfuseTelemetry(trace_id="trace-1")
        telemetry.on_router_event(
            {
                "event": "ok",
                "task": "coding",
                "ref": "deepseek/deepseek-chat",
                "attempt": 1,
                "latency_ms": 612.3,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "total_tokens": 140,
                },
            }
        )
        assert telemetry.flush() is True

        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["url"] == "https://cloud.langfuse.com/api/public/ingestion"
        expected_auth = base64.b64encode(b"pk-test-123:sk-test-456").decode()
        assert call["headers"]["Authorization"] == f"Basic {expected_auth}"

        batch = call["body"]["batch"]
        assert len(batch) == 1
        record = batch[0]
        assert record["type"] == "generation-create"
        assert record["traceId"] == "trace-1"
        assert record["usage"] == {"input": 100, "output": 40, "total": 140}
        assert record["model"] == "deepseek/deepseek-chat"
        assert "rlp:ok" in record["name"]

    def test_error_event_gets_level_and_status(self, recorder, keys):
        telemetry = LangfuseTelemetry()
        telemetry.on_router_event(
            {"event": "error", "task": "coding", "ref": "zai/glm",
             "error": "auth failed", "kind": "auth", "attempt": 1}
        )
        telemetry.flush()

        record = recorder.calls[0]["body"]["batch"][0]
        assert record["level"] == "ERROR"
        assert record["statusMessage"] == "auth failed"

    def test_skip_event_is_debug_level(self, recorder, keys):
        telemetry = LangfuseTelemetry()
        telemetry.on_router_event(
            {"event": "skip", "task": "coding", "ref": "gigachat/x",
             "reason": "provider not configured"}
        )
        telemetry.flush()

        record = recorder.calls[0]["body"]["batch"][0]
        assert record["level"] == "DEBUG"
        assert "usage" not in record  # skip has no tokens to report

    def test_non_scalar_metadata_is_stringified(self, recorder, keys):
        telemetry = LangfuseTelemetry()
        telemetry.on_router_event(
            {"event": "ok", "ref": "d/x", "usage": {"input_tokens": 1}}
        )
        telemetry.flush()

        record = recorder.calls[0]["body"]["batch"][0]
        # usage dict was stringified inside metadata (JSON-safe), not dropped
        assert "usage" in record["metadata"]


class TestIterations:
    def test_iteration_maps_tokens_and_judge(self, recorder, keys):
        telemetry = LangfuseTelemetry(trace_id="t", trace_name="eval")
        telemetry.on_iteration(make_log())
        telemetry.flush()

        record = recorder.calls[0]["body"]["batch"][0]
        assert record["name"] == "eval:iteration-1"
        assert record["model"] == "deepseek/deepseek-chat"
        assert record["usage"] == {"input": 100, "output": 50, "total": 150}
        meta = record["metadata"]
        assert meta["kind"] == "iteration"
        assert meta["judge_verdict"] is False
        assert meta["judge_ms"] == 300.0
        assert meta["diagnostics"] == ["module.bsl:4:1: ParseError: сломано"]
        assert record["statusMessage"] == "judge rejected the module"

    def test_verified_false_is_warning(self, recorder, keys):
        telemetry = LangfuseTelemetry()
        telemetry.on_iteration(make_log(verified=False))
        telemetry.flush()
        assert recorder.calls[0]["body"]["batch"][0]["level"] == "WARNING"

    def test_no_diagnostics_no_key(self, recorder, keys):
        telemetry = LangfuseTelemetry()
        telemetry.on_iteration(make_log(diagnostics=[]))
        telemetry.flush()
        assert "diagnostics" not in recorder.calls[0]["body"]["batch"][0]["metadata"]


class TestFlushing:
    def test_autoflush_on_threshold(self, recorder, keys):
        telemetry = LangfuseTelemetry(flush_every=2)
        for i in range(4):
            telemetry.on_router_event({"event": "skip", "ref": f"p/{i}"})
        # 4 events with flush_every=2 -> 2 auto-flushes, buffer empty
        assert telemetry.sent_events == 4
        assert telemetry.pending() == 0
        assert len(recorder.calls) == 2

    def test_send_error_swallowed_and_counted(self, keys, monkeypatch):
        def broken(url, headers, body, timeout):
            raise OSError("network down")

        monkeypatch.setattr(telemetry_module, "_http_post", broken)
        telemetry = LangfuseTelemetry()
        telemetry.on_router_event({"event": "ok", "ref": "d/x"})
        assert telemetry.flush() is False
        assert telemetry.send_errors == 1
        assert telemetry.pending() == 1  # buffer kept for the next attempt

    def test_custom_host_from_env(self, recorder, keys, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        telemetry = LangfuseTelemetry()
        telemetry.on_router_event({"event": "skip", "ref": "d/x"})
        telemetry.flush()
        assert recorder.calls[0]["url"].startswith("http://localhost:3000/")

    def test_flush_without_events_is_false(self, recorder, keys):
        telemetry = LangfuseTelemetry()
        assert telemetry.flush() is False


class TestFactory:
    def test_from_env_returns_adapter(self):
        adapter = telemetry_from_env(trace_id="run-x")
        assert isinstance(adapter, LangfuseTelemetry)
        assert adapter.trace_id == "run-x"

    def test_unique_trace_ids(self):
        assert telemetry_from_env().trace_id != telemetry_from_env().trace_id
