"""An audit projects model failures without transmitting provider details."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from mira.ai.audit import ReviewTrace


async def test_review_trace_counts_skips_separately_from_actual_model_usage():
    trace = ReviewTrace(SimpleNamespace(submit=MagicMock()), 42, 1, 1)
    now = time.time()
    quota = {
        "result": "skipped",
        "provider": "claude-cli",
        "model": "test-model",
        "cause": "quota",
        "action": "private provider details must not be rendered",
    }
    timeout = {
        "result": "failed",
        "provider": "codex-cli",
        "model": "test-fallback",
        "cause": "timeout",
        "duration_ms": 3200,
        "session_wait_ms": 200,
        "inference_ms": 3000,
    }
    session_timeout = {
        **timeout,
        "cause": "session_timeout",
        "duration_ms": 1000,
        "session_wait_ms": 1000,
        "inference_ms": 0,
    }
    trace.observe(
        {
            "id": "scan",
            "scenario": "security",
            "created_at": now,
            "finished_at": now,
            "status": "failed",
            "steps": [quota, quota, session_timeout, timeout],
        }
    )
    trace.finish("completed")
    stage = next(item for item in trace.snapshot()["stages"] if item["id"] == "security")
    assert stage["status"] == "failed"
    assert [model["model"] for model in stage["models"]] == ["test-fallback"]
    assert stage["models"][0]["session_wait_ms"] == 1200
    assert stage["models"][0]["inference_ms"] == 3000
    assert [(issue["cause"], issue["count"]) for issue in stage["issues"]] == [
        ("quota", 2),
        ("session_timeout", 1),
        ("timeout", 1),
    ]
    markdown = trace.markdown(final=True)
    assert "Quota unavailable" in markdown
    assert "Connection wait timed out" in markdown
    assert "Model response timed out" in markdown
    assert "wait 1.2s / model 3.0s" in markdown
    assert "private provider details" not in markdown


async def test_review_trace_reports_active_attempt_before_times_are_available():
    trace = ReviewTrace(SimpleNamespace(submit=MagicMock()), 42, 1, 2)
    trace.observe(
        {
            "id": "scan",
            "scenario": "security",
            "created_at": time.time(),
            "status": "running",
            "steps": [
                {
                    "result": "running",
                    "phase": "waiting_for_session",
                    "provider": "codex-cli",
                    "model": "test-model",
                    "session_wait_ms": 0,
                    "inference_ms": 0,
                }
            ],
        }
    )
    stage = next(item for item in trace.snapshot()["stages"] if item["id"] == "security")
    assert stage["models"][0]["active_attempts"] == 1
    markdown = trace.markdown()
    assert "Waiting for connection" in markdown
    assert "wait 0.0s" not in markdown
    assert "model 0.0s" not in markdown
