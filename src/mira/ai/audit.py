"""Buffered review telemetry and the status of one PR audit attempt."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

if TYPE_CHECKING:
    from mira.ai.store import AIStore

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

AUDIT_STAGES = {
    "ingestion": "Ingestion and context",
    "indexing": "Indexing",
    "review": "PR review",
    "security": "Security",
    "dependencies": "Dependencies",
    "walkthrough": "Walkthrough",
    "summary": "Summary",
    "self_critique": "Self-critique",
    "fix_verification": "Fix verification",
    "overlap": "PR overlap",
}

ISSUE_LABELS = {
    "access": "Model unavailable",
    "auth": "Connection needs authentication",
    "capacity_busy": "Connection in use by another review",
    "connection_disabled": "Connection disabled",
    "context_incompatible": "Context limit exceeded",
    "invalid": "Invalid model response",
    "provider": "Provider error",
    "quota": "Quota unavailable",
    "reasoning_incompatible": "Reasoning level unsupported",
    "timeout": "Timed out",
    "tools_incompatible": "Tool use unsupported",
    "vault": "Credential storage unavailable",
}


class AuditWriter:
    """Coalesce live updates and persist small batches off the request loop."""

    def __init__(self, store: AIStore, interval: float = 0.5) -> None:
        self.store = store
        self.interval = interval
        self.pending: dict[str, dict] = {}
        self.task: asyncio.Task | None = None
        self.executor: ThreadPoolExecutor | None = None

    def submit(self, snapshot: dict) -> None:
        key = snapshot["id"]
        if key not in self.pending and len(self.pending) >= 2048:
            if snapshot["status"] == "running":
                return
            oldest_key = next(iter(self.pending))
            self.pending.pop(oldest_key)
            logger.warning("Audit buffer overflow; dropped its oldest queued update")
        self.pending[key] = copy.deepcopy(snapshot)
        if self.task is None or self.task.done():
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mira-audit")
            self.task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        assert self.executor is not None
        while self.pending:
            await asyncio.sleep(self.interval)
            batch = dict(list(self.pending.items())[:64])
            for key in batch:
                self.pending.pop(key)
            try:
                await asyncio.get_running_loop().run_in_executor(
                    self.executor, self.store.save_runs, list(batch.values())
                )
            except Exception as exc:
                logger.warning("Audit batch deferred: %s", type(exc).__name__)
                # Keep newer versions of rows produced while this batch was in transit.
                for key, row in batch.items():
                    if len(self.pending) < 2048:
                        self.pending.setdefault(key, row)
                await asyncio.sleep(2)

    async def flush(self) -> None:
        if self.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=5)
            except TimeoutError:
                logger.warning("Audit writer did not drain before shutdown")

    async def close(self) -> None:
        await self.flush()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None


class ReviewTrace:
    """Aggregate concurrent provider attempts and local spans by audit stage."""

    def __init__(self, writer: AuditWriter, job_id: int, attempt: int, worker: int | None) -> None:
        self.writer = writer
        self.id = f"audit:{job_id}:{attempt}:{uuid4().hex}"
        self.job_id = job_id
        self.attempt = attempt
        self.worker = worker
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.status = "running"
        self.runs: dict[str, dict] = {}
        self.spans: dict[str, list[dict]] = {}
        self.failed_stages: set[str] = set()
        self.changed = asyncio.Event()
        self.stop = asyncio.Event()
        self.progress_task: asyncio.Task | None = None
        self._snapshot_timer: asyncio.TimerHandle | None = None
        self.emit()

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        span = {"started_at": time.time(), "status": "running", "finished_at": None}
        self.spans.setdefault(stage, []).append(span)
        self.emit()
        try:
            yield
        except asyncio.CancelledError:
            span["status"] = "cancelled"
            raise
        except Exception:
            span["status"] = "failed"
            raise
        else:
            span["status"] = "success"
        finally:
            span["finished_at"] = time.time()
            self.emit()

    async def track(self, stage: str, work: Awaitable[_T]) -> _T:
        with self.measure(stage):
            return await work

    def stage_failed(self, stage: str) -> None:
        self.failed_stages.add(stage)
        self.emit()

    def observe(self, run: dict) -> None:
        self.runs[run["id"]] = run
        self.writer.submit(run)
        self.emit()

    def emit(self) -> None:
        self.changed.set()
        if self.finished_at is not None:
            if self._snapshot_timer is not None:
                self._snapshot_timer.cancel()
            self._persist()
        elif self._snapshot_timer is None:
            self._snapshot_timer = asyncio.get_running_loop().call_later(0.25, self._persist)

    def _persist(self) -> None:
        self._snapshot_timer = None
        self.writer.submit(self.snapshot())

    def record_job_metrics(self, metrics: dict) -> None:
        self.writer.submit(
            {
                "id": f"metrics:{self.id}",
                "status": "completed",
                "created_at": self.started_at,
                "job_id": self.job_id,
                "attempt": self.attempt,
                "job_metrics": metrics,
            }
        )

    def finish(self, status: str) -> None:
        if self.finished_at is not None and self.status == status:
            return
        self.status = status
        self.finished_at = time.time()
        self.emit()

    def snapshot(self) -> dict:
        now = self.finished_at or time.time()
        stages = []
        for stage_id, label in AUDIT_STAGES.items():
            runs = [run for run in self.runs.values() if run["scenario"] == stage_id]
            sources = self.spans.get(stage_id, []) + [
                {
                    "started_at": run["created_at"],
                    "finished_at": run.get("finished_at"),
                    "status": run["status"],
                }
                for run in runs
            ]
            started = min((source["started_at"] for source in sources), default=None)
            ended = max((source["finished_at"] or now for source in sources), default=None)
            states = {source["status"] for source in sources}
            status = (
                "running"
                if "running" in states and self.status == "running"
                else "failed"
                if "failed" in states or stage_id in self.failed_stages
                else "cancelled"
                if "cancelled" in states or "running" in states
                else "completed"
                if sources
                else "skipped"
                if self.finished_at is not None
                else "pending"
            )
            models: dict[tuple[str, str], dict] = {}
            issues: dict[tuple[str, str, str, str], dict] = {}
            for run in runs:
                for step in run["steps"]:
                    provider, model = step["provider"], step["model"]
                    cause = step.get("cause", "")
                    if cause in ISSUE_LABELS and step["result"] in {"failed", "skipped"}:
                        issue = issues.setdefault(
                            (provider, model, cause, step["result"]),
                            {
                                "provider": provider,
                                "model": model,
                                "cause": cause,
                                "label": ISSUE_LABELS[cause],
                                "result": step["result"],
                                "count": 0,
                            },
                        )
                        issue["count"] += 1
                    if step["result"] == "skipped":
                        continue
                    item = models.setdefault(
                        (provider, model),
                        {
                            "provider": provider,
                            "model": model,
                            "calls": 0,
                            "duration_ms": 0,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "usage_reported": False,
                            "calls_with_usage": 0,
                        },
                    )
                    usage = step.get("usage") or {}
                    item["calls"] += 1
                    item["duration_ms"] += step.get("duration_ms", 0)
                    item["input_tokens"] += usage.get("prompt_tokens", 0)
                    item["output_tokens"] += usage.get("completion_tokens", 0)
                    item["usage_reported"] |= bool(usage)
                    item["calls_with_usage"] += int(bool(usage))
            phases = {
                step["phase"]
                for run in runs
                if run["status"] == "running"
                for step in run["steps"]
                if step["result"] == "running"
            }
            stages.append(
                {
                    "id": stage_id,
                    "label": label,
                    "status": status,
                    "duration_ms": max(0, int((ended - started) * 1000))
                    if started is not None and ended is not None
                    else None,
                    "started_at": started,
                    "finished_at": ended if status != "running" else None,
                    "models": list(models.values()),
                    "issues": list(issues.values()),
                    "phases": sorted(phases),
                }
            )
        return {
            "id": self.id,
            "job_id": str(self.job_id),
            "attempt": self.attempt,
            "worker": self.worker,
            "created_at": self.started_at,
            "updated_at": now,
            "finished_at": self.finished_at,
            "status": self.status,
            "stages": stages,
        }

    def markdown(self, *, final: bool = False) -> str:
        snapshot = self.snapshot()
        running = [s for s in snapshot["stages"] if s["status"] == "running"]
        lines = ["### Audit progress", ""]
        if not final:
            started = time.strftime("%H:%M:%S UTC", time.gmtime(self.started_at))
            names = ", ".join(stage["label"] for stage in running)
            lines.extend([f"**Audit started** · {started}" + (f" · {names}" if names else ""), ""])
        lines.extend(
            ["| Stage | Status | Model | Time | Tokens in / out |", "|---|---|---|---:|---:|"]
        )
        for stage in snapshot["stages"]:
            status = stage["status"]
            if final and status == "pending":
                status = "skipped"
            status_text = status.title()
            if status == "failed" and stage["issues"]:
                labels = sorted({issue["label"] for issue in stage["issues"]})
                status_text += " · " + ", ".join(labels)
            if status == "running":
                phases = stage["phases"]
                if "requesting" in phases:
                    status_text = "Waiting for model response"
                elif "waiting_for_session" in phases:
                    status_text = "Waiting for connection"
            models = stage["models"]
            model_text = ", ".join(f"`{model['model']}`" for model in models) or "—"
            duration = stage["duration_ms"]
            elapsed = f"{duration / 1000:.1f}s" if duration is not None else "—"
            reported = [model for model in models if model["usage_reported"]]
            usage = (
                f"{sum(model['input_tokens'] for model in reported):,} / "
                f"{sum(model['output_tokens'] for model in reported):,}"
                if reported
                else "—"
            )
            if reported and any(model["calls_with_usage"] < model["calls"] for model in models):
                usage = f"≥ {usage}"
            lines.append(
                f"| {stage['label']} | {status_text} | {model_text} | {elapsed} | {usage} |"
            )
        return "\n".join(lines)

    def bind_progress(
        self, publish: Callable[[str], Awaitable[None]], interval: float = 10
    ) -> None:
        async def sender() -> None:
            while not self.stop.is_set():
                await self.changed.wait()
                if self.stop.is_set():
                    return
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=interval)
                except TimeoutError:
                    if self.stop.is_set():
                        return
                    self.changed.clear()
                    try:
                        # The GitHub provider edits comments in a thread. Wait for
                        # the edit's own transport timeout rather than cancelling
                        # its coroutine while it can still overwrite the final report.
                        await publish(self.markdown())
                    except Exception as exc:
                        logger.debug("Unable to update PR progress: %s", type(exc).__name__)

        self.progress_task = asyncio.create_task(sender())

    async def stop_progress(self) -> None:
        self.stop.set()
        self.changed.set()
        if self.progress_task is not None:
            await asyncio.shield(self.progress_task)
