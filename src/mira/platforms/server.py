"""FastAPI webhook server — serves GitHub, GitLab, Forgejo and/or Bitbucket
plus the dashboard UI.

Each platform's events are verified and dispatched by its own module
(``platforms.github.webhook`` / ``platforms.gitlab.webhook``); this file is just
the thin wiring: two symmetric routes (verify → dispatch → response), the
background lifespan, and the dashboard + SPA mount.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mira.ai.audit import ReviewTrace
from mira.config import MiraConfig, ReviewConfig, load_config
from mira.platforms.github.auth import GitHubAppAuth
from mira.platforms.github.webhook import (
    _verify_signature,
    backfill_missing_indexes,
    dispatch_github_event,
)

logger = logging.getLogger(__name__)

_SAFE_BOT_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
_REVIEW_CANCEL_POLL_SECONDS = 1.0
_REVIEW_PR_POLL_SECONDS = 30.0
_REVIEW_WORKER_RESTART_SECONDS = 5.0


async def _supervise_review_worker(app_auth: GitHubAppAuth, bot_name: str) -> None:
    while True:
        try:
            await _run_review_worker(app_auth, bot_name)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            logger.warning("Review worker was cancelled unexpectedly; recovering queue")
        except Exception as exc:
            logger.warning("Review worker stopped (%s); recovering queue", type(exc).__name__)
        await asyncio.sleep(_REVIEW_WORKER_RESTART_SECONDS)


def _review_job_can_continue(db: Any, job: Any, info: Any, policy: ReviewConfig) -> bool:
    if not db.is_review_job_current(job.id):
        return False
    if info.merged or info.state != "open" or (policy.skip_draft_prs and info.draft):
        reason = "pr_merged" if info.merged else "pr_closed" if info.state != "open" else "pr_draft"
        db.cancel_pr_review_jobs(
            job.owner,
            job.repo,
            job.pr_number,
            reason=reason,
            platform=job.platform,
        )
        return False
    if not info.head_sha:
        raise RuntimeError("GitHub did not return the PR head SHA")
    if info.head_sha != job.head_sha:
        if policy.review_on_synchronize:
            db.enqueue_review_job(
                owner=job.owner,
                repo=job.repo,
                pr_number=job.pr_number,
                head_sha=info.head_sha,
                pr_url=job.pr_url,
                pr_title=info.title,
                installation_id=job.installation_id,
                is_private=bool(job.is_private),
                platform=job.platform,
                defer_seconds=policy.synchronize_debounce_seconds,
                authoritative=True,
            )
        db.supersede_review_job(job.id)
        return False
    return True


async def _run_cancellable_review(
    db: Any,
    job: Any,
    provider: Any,
    bot_name: str,
    *,
    policy: ReviewConfig | None = None,
    worker: int | None = None,
    trace: ReviewTrace | None = None,
) -> bool:
    from mira.platforms.handlers import run_pr_review

    policy = policy or ReviewConfig()
    probe = asyncio.wait_for(provider.get_pr_info(job.pr_url), timeout=10)
    info = await trace.track("ingestion", probe) if trace else await probe
    if not _review_job_can_continue(db, job, info, policy):
        db.complete_review_job_cancellation(job.id)
        return True

    async def watch() -> None:
        next_pr_check = asyncio.get_running_loop().time() + _REVIEW_PR_POLL_SECONDS
        while db.is_review_job_current(job.id):
            await asyncio.sleep(_REVIEW_CANCEL_POLL_SECONDS)
            if not db.is_review_job_current(job.id):
                return
            if asyncio.get_running_loop().time() >= next_pr_check:
                try:
                    current = await asyncio.wait_for(provider.get_pr_info(job.pr_url), timeout=10)
                except Exception as exc:
                    logger.warning(
                        "Unable to check PR lifecycle for job %s: %s", job.id, type(exc).__name__
                    )
                else:
                    if not _review_job_can_continue(db, job, current, policy):
                        return
                next_pr_check = asyncio.get_running_loop().time() + _REVIEW_PR_POLL_SECONDS

    review = asyncio.create_task(
        run_pr_review(
            provider,
            job.owner,
            job.repo,
            job.pr_number,
            job.pr_url,
            bool(job.is_private),
            bot_name,
            platform=job.platform,
            pr_title=job.pr_title,
            publish_guard=lambda: db.is_review_job_current(job.id),
            job_id=job.id,
            pr_info=info,
            worker=worker,
            trace=trace,
        )
    )
    monitor = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((review, monitor), return_when=asyncio.FIRST_COMPLETED)
        if monitor in done:
            await monitor
            return True
        return await review
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        raise RuntimeError("Review task was cancelled unexpectedly") from None
    finally:
        for task in (review, monitor):
            if not task.done():
                task.cancel()
        await asyncio.gather(review, monitor, return_exceptions=True)
        db.complete_review_job_cancellation(job.id)


async def _run_review_worker(app_auth: GitHubAppAuth, bot_name: str) -> None:
    """Own queue recovery once and then run its consumers under the same lease."""
    from mira.ai.api import get_store
    from mira.dashboard.api import _app_db

    async with get_store(_app_db).session_lock("github-review-queue-supervisor"):
        config = load_config()
        recovered = _app_db.recover_interrupted_review_jobs()
        if recovered:
            logger.info("Requeued %s interrupted review job(s)", recovered)
        logger.info("Review queue started with %s worker slots", config.review.queue_workers)
        async with asyncio.TaskGroup() as tasks:
            for worker in range(1, config.review.queue_workers + 1):
                tasks.create_task(_consume_review_jobs(app_auth, bot_name, config, worker=worker))


async def _consume_review_jobs(
    app_auth: GitHubAppAuth,
    bot_name: str,
    config: MiraConfig,
    *,
    worker: int,
) -> None:
    from mira.ai.runtime import current
    from mira.dashboard.api import _app_db
    from mira.exceptions import LLMError, NonRetriableLLMError
    from mira.providers import create_provider

    waiting_for_capacity = False
    while True:
        if worker > 1 and _app_db.has_claimable_review_jobs():
            runtime = current()
            if runtime is None or not runtime.managed() or runtime.review_capacity() < worker:
                if not waiting_for_capacity:
                    logger.info(
                        "Review worker %s waiting for another available AI connection", worker
                    )
                    waiting_for_capacity = True
                await asyncio.sleep(_REVIEW_WORKER_RESTART_SECONDS)
                continue
            waiting_for_capacity = False
        job = _app_db.claim_next_review_job()
        if job is None:
            await asyncio.sleep(1)
            continue
        if not _app_db.is_review_job_current(job.id):
            # A synchronize event superseded it after it was claimed.
            continue
        runtime = current()
        trace = ReviewTrace(runtime.audit_writer, job.id, job.attempts, worker) if runtime else None
        outcome = "failed"
        try:
            provider = create_provider(
                "github", partial(app_auth.get_installation_token, job.installation_id)
            )
            logger.info("Review worker %s claimed job %s", worker, job.id)
            reviewed = await _run_cancellable_review(
                _app_db,
                job,
                provider,
                bot_name,
                policy=config.review,
                worker=worker,
                trace=trace,
            )
            if not reviewed:
                # Another SHA may still be completing. Return this job to the
                # queue so the latest revision is not silently dropped.
                _app_db.finish_review_job(
                    job.id, error="review already running", retry=job.attempts < 3
                )
                continue
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except NonRetriableLLMError as exc:
            _app_db.finish_review_job(job.id, error=f"{exc.code}: {exc.safe_message}")
            logger.warning("Review job %s failed without retry: %s", job.id, exc.safe_message)
        except LLMError as exc:
            _app_db.finish_review_job(
                job.id,
                error=f"{exc.code}: {exc.safe_message}",
                retry=job.attempts < 3,
            )
            logger.warning(
                "Review job %s failed (attempt %s): %s",
                job.id,
                job.attempts,
                exc.code,
            )
        except Exception as exc:  # transient host/provider failure; bounded retry
            _app_db.finish_review_job(job.id, error=type(exc).__name__, retry=job.attempts < 3)
            logger.warning(
                "Review job %s failed (attempt %s): %s", job.id, job.attempts, type(exc).__name__
            )
        else:
            outcome = "completed" if _app_db.is_review_job_current(job.id) else "cancelled"
            _app_db.finish_review_job(job.id)
        finally:
            if trace is not None:
                trace.finish(outcome)
                await trace.stop_progress()


def _json_status(status: str) -> Response:
    return Response(
        content=f'{{"status": "{status}"}}', status_code=200, media_type="application/json"
    )


def create_app(
    app_auth: GitHubAppAuth | None = None,
    webhook_secret: str | None = None,
    bot_name: str = "miracodeai",
    *,
    gitlab_auth: Any = None,
    gitlab_webhook_secret: str | None = None,
    forgejo_auth: Any = None,
    forgejo_webhook_secret: str | None = None,
    bitbucket_auth: Any = None,
    bitbucket_webhook_secret: str | None = None,
) -> FastAPI:
    """Create the FastAPI app. GitHub (``app_auth`` + ``webhook_secret``) and
    GitLab (``gitlab_auth`` + ``gitlab_webhook_secret``) routes each activate
    only when their creds are supplied, so a deployment can serve one or both.
    """
    if not _SAFE_BOT_NAME.match(bot_name):
        raise ValueError(f"Invalid bot_name {bot_name!r}: must match [a-zA-Z0-9_-]+")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Backfill is a GitHub-installation concept; skip it on GitLab-only.
        backfill_task = (
            asyncio.create_task(backfill_missing_indexes(app_auth))
            if app_auth is not None
            else None
        )
        if backfill_task is not None:
            backfill_task.add_done_callback(
                lambda t: (
                    logger.warning("Backfill failed: %s", t.exception()) if t.exception() else None
                )
            )

        review_worker_task = (
            asyncio.create_task(_supervise_review_worker(app_auth, bot_name))
            if app_auth is not None
            else None
        )

        # GitLab's equivalent: discover the projects the token can access and
        # register them so they're in the dashboard ready to index up front.
        if gitlab_auth is not None:
            from mira.platforms.gitlab.webhook import backfill_gitlab_projects

            gl_task = asyncio.create_task(backfill_gitlab_projects(gitlab_auth))
            gl_task.add_done_callback(
                lambda t: (
                    logger.warning("GitLab discovery failed: %s", t.exception())
                    if t.exception()
                    else None
                )
            )

        # Forgejo's equivalent: discover repos the token can access and
        # register them so they're in the dashboard ready to index up front.
        if forgejo_auth is not None:
            from mira.platforms.forgejo.webhook import backfill_forgejo_repos

            fj_task = asyncio.create_task(backfill_forgejo_repos(forgejo_auth))
            fj_task.add_done_callback(
                lambda t: (
                    logger.warning("Forgejo discovery failed: %s", t.exception())
                    if t.exception()
                    else None
                )
            )

        # Bitbucket's equivalent: discover the repos the credential can access.
        bb_task = None
        if bitbucket_auth is not None:
            from mira.platforms.bitbucket.webhook import backfill_bitbucket_repos

            bb_task = asyncio.create_task(backfill_bitbucket_repos(bitbucket_auth))
            bb_task.add_done_callback(
                lambda t: (
                    logger.warning("Bitbucket discovery failed: %s", t.exception())
                    if not t.cancelled() and t.exception()
                    else None
                )
            )

        from mira.security.poller import run_forever as run_vuln_poller

        vuln_task = asyncio.create_task(run_vuln_poller())
        vuln_task.add_done_callback(
            lambda t: (
                logger.warning("Vuln poller crashed: %s", t.exception())
                if t.exception() and not t.cancelled()
                else None
            )
        )

        yield
        if backfill_task is not None and not backfill_task.done():
            backfill_task.cancel()
        if review_worker_task is not None and not review_worker_task.done():
            review_worker_task.cancel()
            await asyncio.gather(review_worker_task, return_exceptions=True)
            from mira.ai.runtime import current

            runtime = current()
            if runtime is not None:
                await runtime.audit_writer.close()
        if forgejo_auth is not None and not fj_task.done():
            fj_task.cancel()
        if bb_task is not None and not bb_task.done():
            bb_task.cancel()
        if not vuln_task.done():
            vuln_task.cancel()

    app = FastAPI(title="Mira", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
            from mira.db.postgres import connect

            conn = None
            try:
                conn = connect(db_url)
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                logger.exception("Health check: database unavailable")
                raise HTTPException(status_code=503, detail="database unavailable") from None
            finally:
                if conn is not None:
                    with suppress(Exception):
                        conn.close()
        return {"status": "ok"}

    # `/webhook` is a deprecated alias from before the `/github/webhook` rename.
    @app.post("/github/webhook")
    @app.post("/webhook")
    async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
        if app_auth is None or webhook_secret is None:
            raise HTTPException(status_code=404)
        payload_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(payload_bytes, signature, webhook_secret):
            return Response(
                content='{"error": "invalid signature"}',
                status_code=401,
                media_type="application/json",
            )
        event = request.headers.get("X-GitHub-Event", "")
        payload: dict[str, Any] = await request.json()
        status = await dispatch_github_event(event, payload, app_auth, bot_name, background_tasks)
        return _json_status(status)

    if gitlab_auth is not None and gitlab_webhook_secret is not None:
        from mira.platforms.gitlab.webhook import dispatch_gitlab_event, verify_gitlab_token

        @app.post("/gitlab/webhook")
        async def gitlab_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
            token = request.headers.get("X-Gitlab-Token", "")
            if not verify_gitlab_token(token, gitlab_webhook_secret):
                return Response(
                    content='{"error": "invalid token"}',
                    status_code=401,
                    media_type="application/json",
                )
            event = request.headers.get("X-Gitlab-Event", "")
            payload = await request.json()
            status = await dispatch_gitlab_event(
                event, payload, gitlab_auth, bot_name, background_tasks
            )
            return _json_status(status)

    if forgejo_auth is not None and forgejo_webhook_secret is not None:
        from mira.platforms.forgejo.webhook import (
            dispatch_forgejo_event,
            verify_forgejo_signature,
        )

        @app.post("/forgejo/webhook")
        async def forgejo_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
            body = await request.body()
            signature = request.headers.get("X-Forgejo-Signature", "")
            if not verify_forgejo_signature(signature, body, forgejo_webhook_secret):
                return Response(
                    content='{"error": "invalid signature"}',
                    status_code=401,
                    media_type="application/json",
                )
            event = request.headers.get("X-Forgejo-Event", "")
            payload = await request.json()
            status = await dispatch_forgejo_event(
                event, payload, forgejo_auth, bot_name, background_tasks
            )
            return _json_status(status)

    if bitbucket_auth is not None and bitbucket_webhook_secret is not None:
        from mira.platforms.bitbucket.webhook import (
            dispatch_bitbucket_event,
            verify_bitbucket_signature,
        )

        @app.post("/bitbucket/webhook")
        async def bitbucket_webhook(
            request: Request, background_tasks: BackgroundTasks
        ) -> Response:
            body = await request.body()
            signature = request.headers.get("X-Hub-Signature", "")
            if not verify_bitbucket_signature(signature, body, bitbucket_webhook_secret):
                return Response(
                    content='{"error": "invalid signature"}',
                    status_code=401,
                    media_type="application/json",
                )
            event = request.headers.get("X-Event-Key", "")
            payload = await request.json()
            status = await dispatch_bitbucket_event(
                event, payload, bitbucket_auth, bot_name, background_tasks
            )
            return _json_status(status)

    from mira.dashboard.api import register_dashboard

    register_dashboard(app)

    # UI dist resolution order: env override → Docker image path → repo-local.
    ui_dist_env = os.environ.get("MIRA_UI_DIST")
    candidates: list[Path] = []
    if ui_dist_env:
        candidates.append(Path(ui_dist_env))
    candidates.extend(
        [
            Path("/app/ui_dist"),
            Path(__file__).resolve().parents[3] / "ui" / "mira" / "dist",
        ]
    )
    ui_dist = next((p for p in candidates if p.is_dir()), None)

    if ui_dist is not None:
        assets_dir = ui_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        index_html = ui_dist / "index.html"

        ui_root = ui_dist.resolve()

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> Response:
            # Don't let the SPA shell swallow misspelled API/webhook paths.
            if full_path.startswith("api/") or full_path in {"webhook", "health"}:
                raise HTTPException(status_code=404)
            file_path = (ui_dist / full_path).resolve()
            # Keep `..` traversal from escaping the dist dir into arbitrary files.
            if file_path.is_relative_to(ui_root) and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(index_html)

    return app
