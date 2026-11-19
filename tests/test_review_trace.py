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
        "duration_ms": 12,
    }
    trace.observe(
        {
            "id": "scan",
            "scenario": "security",
            "created_at": now,
            "finished_at": now,
            "status": "failed",
            "steps": [quota, quota, timeout],
        }
    )
    trace.finish("completed")
    stage = next(item for item in trace.snapshot()["stages"] if item["id"] == "security")
    assert stage["status"] == "failed"
    assert [model["model"] for model in stage["models"]] == ["test-fallback"]
    assert [(issue["cause"], issue["count"]) for issue in stage["issues"]] == [
        ("quota", 2),
        ("timeout", 1),
    ]
    assert "Quota unavailable" in trace.markdown(final=True)
    assert "private provider details" not in trace.markdown(final=True)
