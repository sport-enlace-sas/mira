"""Regression coverage for the in-process durable PR review queue."""

from __future__ import annotations

from mira.dashboard import db as dashboard_db
from mira.dashboard.db import AppDatabase


def test_new_sha_supersedes_pending_review_and_duplicate_is_idempotent(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    args = {
        "owner": "sport-enlace-sas",
        "repo": "gannar-monorepo",
        "pr_number": 599,
        "pr_url": "https://github.com/sport-enlace-sas/gannar-monorepo/pull/599",
        "pr_title": "Review queue fixture",
        "installation_id": 42,
        "is_private": True,
    }

    assert db.enqueue_review_job(head_sha="a" * 40, **args)
    assert not db.enqueue_review_job(head_sha="a" * 40, **args)
    assert db.enqueue_review_job(head_sha="b" * 40, **args)

    job = db.claim_next_review_job()
    assert job is not None
    assert job.head_sha == "b" * 40
    assert job.attempts == 1
    assert db.is_review_job_current(job.id)
    db.finish_review_job(job.id)
    assert not db.is_review_job_current(job.id)


def test_transient_failure_returns_job_to_queue_after_backoff(tmp_path, monkeypatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(dashboard_db.time, "time", lambda: now)
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o", repo="r", pr_number=1, head_sha="c" * 40,
        pr_url="https://github.com/o/r/pull/1", pr_title="t",
        installation_id=1, is_private=True,
    )
    first = db.claim_next_review_job()
    assert first is not None
    db.finish_review_job(first.id, error="provider timeout", retry=True)
    # First retry is intentionally delayed by 10 seconds rather than looping
    # while a provider is unavailable.
    assert db.claim_next_review_job() is None
    now += 10
    second = db.claim_next_review_job()
    assert second is not None
    assert second.id == first.id
    assert second.attempts == 2


def test_startup_recovers_interrupted_job(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o", repo="r", pr_number=1, head_sha="d" * 40,
        pr_url="https://github.com/o/r/pull/1", pr_title="t",
        installation_id=1, is_private=True,
    )
    assert db.claim_next_review_job() is not None
    assert db.recover_interrupted_review_jobs() == 1
    assert db.claim_next_review_job() is not None


def test_dashboard_job_list_includes_provider_provenance(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o", repo="r", pr_number=2, head_sha="e" * 40,
        pr_url="https://github.com/o/r/pull/2", pr_title="provider fixture",
        installation_id=1, is_private=True,
    )
    job = db.claim_next_review_job()
    assert job is not None
    db.set_review_job_execution(job.id, provider_used="codex-cli", fallback_used=True)
    db.finish_review_job(job.id)

    [listed] = db.list_review_jobs()
    assert listed.status == "completed"
    assert listed.provider_used == "codex-cli"
    assert listed.fallback_used is True
