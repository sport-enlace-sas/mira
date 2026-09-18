"""Regression coverage for the in-process durable PR review queue."""

from __future__ import annotations

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


def test_transient_failure_returns_job_to_queue(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o", repo="r", pr_number=1, head_sha="c" * 40,
        pr_url="https://github.com/o/r/pull/1", pr_title="t",
        installation_id=1, is_private=True,
    )
    first = db.claim_next_review_job()
    assert first is not None
    db.finish_review_job(first.id, error="provider timeout", retry=True)
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
