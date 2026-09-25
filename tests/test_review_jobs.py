"""Regression coverage for the in-process durable PR review queue."""

from __future__ import annotations

import sqlite3

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
        owner="o",
        repo="r",
        pr_number=1,
        head_sha="c" * 40,
        pr_url="https://github.com/o/r/pull/1",
        pr_title="t",
        installation_id=1,
        is_private=True,
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
        owner="o",
        repo="r",
        pr_number=1,
        head_sha="d" * 40,
        pr_url="https://github.com/o/r/pull/1",
        pr_title="t",
        installation_id=1,
        is_private=True,
    )
    assert db.claim_next_review_job() is not None
    assert db.recover_interrupted_review_jobs() == 1
    assert db.claim_next_review_job() is not None


def test_manual_retry_reschedules_failed_job_but_not_superseded_sha(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    args = {
        "owner": "o",
        "repo": "r",
        "pr_number": 1,
        "pr_url": "https://github.com/o/r/pull/1",
        "pr_title": "t",
        "installation_id": 1,
        "is_private": True,
    }
    assert db.enqueue_review_job(head_sha="f" * 40, **args)
    failed = db.claim_next_review_job()
    assert failed is not None
    db.finish_review_job(failed.id, error="provider unavailable")
    assert db.retry_review_job(failed.id)
    assert db.claim_next_review_job() is not None

    # A newer SHA must supersede even a failed job, and a manual retry must
    # never revive that stale SHA.
    db.finish_review_job(failed.id, error="provider unavailable")
    assert db.enqueue_review_job(head_sha="g" * 40, **args)
    assert not db.retry_review_job(failed.id)


def test_dashboard_job_list_includes_provider_provenance(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o",
        repo="r",
        pr_number=2,
        head_sha="e" * 40,
        pr_url="https://github.com/o/r/pull/2",
        pr_title="provider fixture",
        installation_id=1,
        is_private=True,
    )
    job = db.claim_next_review_job()
    assert job is not None
    db.set_review_job_execution(
        job.id,
        provider_used="codex-cli",
        fallback_used=True,
        models_attempted="claude-fable-5-1 -> gpt-5.6-sol",
        audit_duration_ms=12_345,
        input_tokens=8_765,
        output_tokens=432,
    )
    db.finish_review_job(job.id)

    [listed] = db.list_review_jobs()
    assert listed.status == "completed"
    assert listed.provider_used == "codex-cli"
    assert listed.fallback_used is True
    assert listed.models_attempted == "claude-fable-5-1 -> gpt-5.6-sol"
    assert listed.audit_duration_ms == 12_345
    assert listed.input_tokens == 8_765
    assert listed.output_tokens == 432


def test_dashboard_job_metrics_accumulate_across_retries(tmp_path, monkeypatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(dashboard_db.time, "time", lambda: now)
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o",
        repo="r",
        pr_number=4,
        head_sha="i" * 40,
        pr_url="https://github.com/o/r/pull/4",
        pr_title="metrics fixture",
        installation_id=1,
        is_private=True,
    )

    first = db.claim_next_review_job()
    assert first is not None
    db.set_review_job_execution(
        first.id,
        provider_used="claude-cli",
        fallback_used=False,
        audit_duration_ms=1_500,
        input_tokens=100,
        output_tokens=20,
    )
    db.finish_review_job(first.id, error="provider timeout", retry=True)

    now += 10
    second = db.claim_next_review_job()
    assert second is not None
    db.set_review_job_execution(
        second.id,
        provider_used="codex-cli",
        fallback_used=True,
        audit_duration_ms=2_500,
        input_tokens=200,
        output_tokens=40,
    )
    db.finish_review_job(second.id)

    [listed] = db.list_review_jobs()
    assert listed.audit_duration_ms == 4_000
    assert listed.input_tokens == 300
    assert listed.output_tokens == 60


def test_existing_review_job_table_adds_execution_metric_columns(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    db = AppDatabase(str(db_path), admin_password="test-password")
    db.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE mira_review_jobs DROP COLUMN audit_duration_ms")
        connection.execute("ALTER TABLE mira_review_jobs DROP COLUMN input_tokens")
        connection.execute("ALTER TABLE mira_review_jobs DROP COLUMN output_tokens")

    migrated = AppDatabase(str(db_path), admin_password="test-password")
    assert migrated._sqlite_conn is not None
    columns = {
        row[1]
        for row in migrated._sqlite_conn.execute("PRAGMA table_info(mira_review_jobs)").fetchall()
    }
    assert {"audit_duration_ms", "input_tokens", "output_tokens"} <= columns


def test_dashboard_job_list_includes_ocr_provenance(tmp_path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="test-password")
    assert db.enqueue_review_job(
        owner="o",
        repo="r",
        pr_number=3,
        head_sha="h" * 40,
        pr_url="https://github.com/o/r/pull/3",
        pr_title="OCR fixture",
        installation_id=1,
        is_private=True,
    )
    job = db.claim_next_review_job()
    assert job is not None
    db.set_review_job_ocr(
        job.id,
        status="degraded",
        version="open-code-review dev linux/amd64",
        duration_ms=91,
        error="invalid_preview",
    )
    db.finish_review_job(job.id)

    [listed] = db.list_review_jobs()
    assert listed.ocr_status == "degraded"
    assert listed.ocr_version == "open-code-review dev linux/amd64"
    assert listed.ocr_duration_ms == 91
    assert listed.ocr_error == "invalid_preview"
