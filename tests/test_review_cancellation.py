"""Cancellation releases the active review before it can be resumed."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from mira.dashboard import api
from mira.dashboard.db import AppDatabase
from mira.models import PRInfo
from mira.platforms import server


@pytest.fixture
def queue_db(tmp_path):
    db = AppDatabase(str(tmp_path / "queue.db"), admin_password="synthetic-test")
    yield db
    db.close()


@pytest.fixture(params=["sqlite", "postgres"])
def persistent_queue(request, tmp_path):
    if request.param == "sqlite":
        url = str(tmp_path / "persistent.db")
        db = AppDatabase(url, admin_password="synthetic-test")
        yield db, url
        db.close()
        return
    url = os.environ.get("MIRA_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("PostgreSQL queue regression runs in GitHub Actions")
    import psycopg
    from psycopg import sql

    schema = "review_cancel_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema}"
    scoped = urlunsplit(parts._replace(query=urlencode(query)))
    db = AppDatabase(scoped, admin_password="synthetic-test")
    try:
        yield db, scoped
    finally:
        db.close()
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def enqueue(db, *, number=938, sha="a" * 40, repo="gannar-monorepo"):
    assert db.enqueue_review_job(
        owner="sport-enlace-sas",
        repo=repo,
        pr_number=number,
        head_sha=sha,
        pr_url=f"https://github.com/sport-enlace-sas/{repo}/pull/{number}",
        pr_title="Synthetic review",
        installation_id=42,
        is_private=True,
    )
    return next(j for j in db.list_review_jobs() if j.pr_number == number and j.head_sha == sha)


def info(job, **changes):
    return PRInfo(
        title=job.pr_title,
        description="",
        base_branch="dev",
        head_branch="synthetic",
        url=job.pr_url,
        number=job.pr_number,
        owner=job.owner,
        repo=job.repo,
        head_sha=job.head_sha,
        **changes,
    )


def test_cancellation_survives_restart_and_resume_waits_for_cleanup(persistent_queue):
    db, url = persistent_queue
    queued = enqueue(db)
    job = db.claim_next_review_job()
    assert db.cancel_review_job(job.id)
    assert db.get_review_job(job.id).status == "cancelling"
    assert not db.is_review_job_current(job.id)
    assert not db.resume_review_job(job.id)
    db.finish_review_job(job.id, error="late timeout", retry=True)
    assert db.get_review_job(job.id).status == "cancelling"
    db.complete_review_job_cancellation(job.id)
    restarted = AppDatabase(url, admin_password="synthetic-test")
    try:
        assert restarted.recover_interrupted_review_jobs() == 0
        assert restarted.get_review_job(job.id).status == "cancelled"
        assert restarted.claim_next_review_job() is None
        assert restarted.resume_review_job(job.id)
        pending = restarted.get_review_job(queued.id)
        assert pending.status == "pending"
        assert pending.attempts == 0
        assert restarted.claim_next_review_job().attempts == 1
    finally:
        restarted.close()


def test_restart_finishes_cancelling_without_retrying_it(persistent_queue):
    db, _ = persistent_queue
    enqueue(db)
    job = db.claim_next_review_job()
    db.cancel_review_job(job.id)
    assert db.recover_interrupted_review_jobs() == 0
    assert db.get_review_job(job.id).status == "cancelled"
    assert db.claim_next_review_job() is None


def test_closed_pr_cancels_pending_without_rewriting_history_or_other_repos(persistent_queue):
    db, _ = persistent_queue
    enqueue(db)
    completed = db.claim_next_review_job()
    db.finish_review_job(completed.id)
    pending = enqueue(db, sha="b" * 40)
    other = enqueue(db, repo="other")
    assert (
        db.cancel_pr_review_jobs("sport-enlace-sas", "gannar-monorepo", 938, reason="pr_merged")
        == 1
    )
    assert db.get_review_job(completed.id).status == "completed"
    assert db.get_review_job(pending.id).status == "cancelled"
    assert db.get_review_job(other.id).status == "pending"
    assert not db.cancel_review_job(completed.id)


def test_new_sha_prevents_resuming_cancelled_history(persistent_queue):
    db, _ = persistent_queue
    old = enqueue(db)
    db.cancel_review_job(old.id)
    current = enqueue(db, sha="b" * 40)
    assert not db.resume_review_job(old.id)
    assert db.get_review_job(old.id).status == "superseded"
    assert db.claim_next_review_job().id == current.id


@pytest.mark.parametrize("merged", [False, True])
async def test_worker_skips_closed_pr_without_calling_model(queue_db, monkeypatch, merged):
    enqueue(queue_db)
    job = queue_db.claim_next_review_job()
    next_job = enqueue(queue_db, number=944)
    review = AsyncMock()
    monkeypatch.setattr("mira.platforms.handlers.run_pr_review", review)
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=info(job, state="closed", merged=merged))
    )
    assert await server._run_cancellable_review(queue_db, job, provider, "mira")
    review.assert_not_awaited()
    cancelled = queue_db.get_review_job(job.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == ("pr_merged" if merged else "pr_closed")
    assert queue_db.claim_next_review_job().id == next_job.id


@pytest.mark.parametrize("stop", ["manual", "merged", "superseded", "shutdown"])
async def test_running_review_is_stopped_and_releases_next_job(queue_db, monkeypatch, stop):
    enqueue(queue_db)
    job = queue_db.claim_next_review_job()
    next_job = enqueue(queue_db, number=944)
    started, released = asyncio.Event(), asyncio.Event()

    async def review(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr("mira.platforms.handlers.run_pr_review", review)
    monkeypatch.setattr(server, "_REVIEW_CANCEL_POLL_SECONDS", 0.005)
    monkeypatch.setattr(server, "_REVIEW_PR_POLL_SECONDS", 0.01)
    provider = SimpleNamespace(get_pr_info=AsyncMock(return_value=info(job)))
    task = asyncio.create_task(server._run_cancellable_review(queue_db, job, provider, "mira"))
    await asyncio.wait_for(started.wait(), 1)
    if stop == "manual":
        assert queue_db.cancel_review_job(job.id)
        assert not queue_db.resume_review_job(job.id)
    elif stop == "merged":
        provider.get_pr_info.return_value = info(job, state="closed", merged=True)
    elif stop == "superseded":
        enqueue(queue_db, sha="c" * 40)
    else:
        task.cancel()
    if stop == "shutdown":
        with pytest.raises(asyncio.CancelledError):
            await task
        assert queue_db.get_review_job(job.id).status == "running"
    else:
        assert await asyncio.wait_for(task, 1)
        assert queue_db.get_review_job(job.id).status == (
            "superseded" if stop == "superseded" else "cancelled"
        )
        queue_db.finish_review_job(job.id, error="late failure", retry=True)
        assert queue_db.claim_next_review_job().id == next_job.id
    assert released.is_set()


async def test_missing_synchronize_event_queues_current_sha(queue_db, monkeypatch):
    enqueue(queue_db)
    job = queue_db.claim_next_review_job()
    review = AsyncMock()
    monkeypatch.setattr("mira.platforms.handlers.run_pr_review", review)
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=replace(info(job), head_sha="b" * 40))
    )
    await server._run_cancellable_review(queue_db, job, provider, "mira")
    review.assert_not_awaited()
    assert queue_db.get_review_job(job.id).status == "superseded"
    assert queue_db.claim_next_review_job().head_sha == "b" * 40


@pytest.mark.parametrize("merged", [False, True])
async def test_closed_webhook_cancels_even_when_a_bot_merges(queue_db, monkeypatch, merged):
    from mira.platforms.github import webhook

    queued = enqueue(queue_db)
    queue_db.claim_next_review_job()
    monkeypatch.setattr(webhook, "_get_app_db", lambda: queue_db)
    payload = {
        "action": "closed",
        "sender": {"login": "mira[bot]"},
        "repository": {"owner": {"login": queued.owner}, "name": queued.repo},
        "pull_request": {"number": queued.pr_number, "merged": merged},
    }
    await webhook.dispatch_github_event(
        "pull_request", payload, SimpleNamespace(), "mira", BackgroundTasks()
    )
    assert queue_db.get_review_job(queued.id).status == "cancelling"
    assert queue_db.get_review_job(queued.id).error == ("pr_merged" if merged else "pr_closed")


@pytest.mark.parametrize("admin", [False, True])
async def test_cancel_resume_api_requires_admin_and_checks_current_pr(queue_db, monkeypatch, admin):
    from mira.dashboard.routers import core

    queued = enqueue(queue_db)
    monkeypatch.setattr(api, "_app_db", queue_db)
    monkeypatch.setattr(
        api, "_build_app_auth", lambda: SimpleNamespace(get_installation_token=AsyncMock())
    )
    provider = SimpleNamespace(get_pr_info=AsyncMock(return_value=info(queued)))
    monkeypatch.setattr("mira.providers.create_provider", lambda *args: provider)
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request: Request, call_next):
        request.state.user = SimpleNamespace(is_admin=admin)
        return await call_next(request)

    app.include_router(core.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cancelled = await client.post(f"/api/review-jobs/{queued.id}/cancel")
        resumed = await client.post(f"/api/review-jobs/{queued.id}/resume")
        if not admin:
            assert cancelled.status_code == resumed.status_code == 403
            provider.get_pr_info.assert_not_awaited()
            return
        assert cancelled.json()["status"] == "cancelled"
        assert resumed.json()["status"] == "pending"
        assert resumed.json()["attempts"] == 0
        assert (await client.post("/api/review-jobs/9999/cancel")).status_code == 404
        queue_db.cancel_review_job(queued.id)
        provider.get_pr_info.return_value = info(queued, state="closed", merged=True)
        assert (await client.post(f"/api/review-jobs/{queued.id}/resume")).status_code == 409
        provider.get_pr_info.return_value = replace(info(queued), head_sha="c" * 40)
        assert (await client.post(f"/api/review-jobs/{queued.id}/resume")).status_code == 409
        assert queue_db.get_review_job(queued.id).status == "cancelled"


async def test_claude_cancellation_terminates_process_tree(monkeypatch):
    from mira.config import LLMConfig
    from mira.llm.claude_cli import ClaudeCLIProvider

    provider = ClaudeCLIProvider(LLMConfig())
    monkeypatch.setattr(provider, "_env", lambda *args: {})
    monkeypatch.setattr(
        provider, "_command", lambda: [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    spawned = asyncio.Event()
    original = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        proc = await original(*args, **kwargs)
        processes.append(proc)
        spawned.set()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(provider._run_codex("Synthetic prompt"))
    await asyncio.wait_for(spawned.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert processes[0].returncode is not None


async def test_handler_cancellation_releases_tracker(queue_db, monkeypatch):
    from mira.config import MiraConfig
    from mira.core.review_status import ReviewTracker
    from mira.platforms import handlers

    started = asyncio.Event()
    tracker = ReviewTracker()

    async def blocked_review(*args):
        started.set()
        await asyncio.Event().wait()

    engine = SimpleNamespace(review_pr=blocked_review, cancel_background_tasks=AsyncMock())
    monkeypatch.setattr(handlers, "ReviewEngine", lambda **kwargs: engine)
    monkeypatch.setattr(handlers, "create_llm", lambda *args: SimpleNamespace())
    monkeypatch.setattr(handlers, "load_config", MiraConfig)
    monkeypatch.setattr(handlers, "review_tracker", tracker)
    monkeypatch.setattr(api, "_app_db", queue_db)
    task = asyncio.create_task(
        handlers.run_pr_review(
            SimpleNamespace(), "o", "r", 1, "https://github.com/o/r/pull/1", True, "mira"
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    assert tracker.get_active()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    engine.cancel_background_tasks.assert_awaited_once()
    assert not tracker.get_active()
    assert tracker.try_start("o/r", 1, "resumed", "url")


async def test_engine_cancellation_stops_walkthrough_and_review_calls(
    sample_diff_text, monkeypatch
):
    from mira.config import MiraConfig
    from mira.core.engine import ReviewEngine

    started = {name: asyncio.Event() for name in ("review", "walkthrough")}
    released = []

    async def blocked(name, *args, **kwargs):
        started[name].set()
        try:
            await asyncio.Event().wait()
        finally:
            released.append(name)

    llm = SimpleNamespace(
        review=lambda *args, **kwargs: blocked("review", *args, **kwargs),
        walkthrough=lambda *args, **kwargs: blocked("walkthrough", *args, **kwargs),
        complete=AsyncMock(),
        count_tokens=lambda _: 100,
        usage={},
    )
    config = MiraConfig()
    config.review.security_pass = False
    config.review.secrets_scan = False
    engine = ReviewEngine(config=config, llm=llm)
    task = asyncio.create_task(engine._review_diff_internal(sample_diff_text))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await engine.cancel_background_tasks()
    assert all(t.done() for t in engine._review_tasks)
    assert set(released) == {"review", "walkthrough"}


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_review_worker_recovers_unexpected_exit_and_honors_shutdown(monkeypatch, failure):
    restarted = asyncio.Event()
    calls = 0

    async def worker(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure()
        restarted.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_run_review_worker", worker)
    monkeypatch.setattr(server, "_REVIEW_WORKER_RESTART_SECONDS", 0.005)
    task = asyncio.create_task(server._supervise_review_worker(SimpleNamespace(), "mira"))
    await asyncio.wait_for(restarted.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 2


async def test_child_cancellation_does_not_cancel_the_worker(queue_db, monkeypatch):
    enqueue(queue_db)
    job = queue_db.claim_next_review_job()

    async def review(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("mira.platforms.handlers.run_pr_review", review)
    provider = SimpleNamespace(get_pr_info=AsyncMock(return_value=info(job)))
    with pytest.raises(RuntimeError, match="cancelled unexpectedly"):
        await server._run_cancellable_review(queue_db, job, provider, "mira")
    queue_db.finish_review_job(job.id, error="RuntimeError", retry=True)
    assert queue_db.get_review_job(job.id).status == "pending"
