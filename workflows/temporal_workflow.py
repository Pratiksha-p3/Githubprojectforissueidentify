"""
workflows/temporal_workflow.py

Optional Temporal wrapper around the existing review pipeline
(app.run_review). The pipeline itself is unchanged — this wraps the whole
thing as a single Temporal activity with automatic retries, so a
transient failure (an API rate limit, a flaky GitHub call) is retried by
Temporal's engine instead of losing the whole review, and the run shows
up in Temporal's UI for observability.

Inert without TEMPORAL_ADDRESS: nothing in app.py imports this module at
startup, and the webhook/CLI paths call app.run_review() directly unless
TEMPORAL_ADDRESS is set (see app.py's _dispatch_review()).

This deliberately wraps run_review() as ONE activity rather than breaking
the pipeline into many fine-grained activities (per-file review, per-check
activity, etc.) — that finer-grained split is real future work once this
is actually exercised against a live Temporal server, not something to
guess at speculatively here.

Install: pip install temporalio
Run a worker:  python -m workflows.temporal_workflow worker
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from config import cfg


async def run_review_activity_impl(params: dict) -> dict:
    """The actual work: call the existing synchronous pipeline in a thread
    so it doesn't block Temporal's async event loop."""
    from app import run_review
    return await asyncio.to_thread(
        run_review,
        repo=params["repo"],
        pr_number=params["pr_number"],
        mock=params.get("mock", False),
        output_dir=params.get("output_dir", "reports"),
        provider=params.get("provider", "github"),
    )


def is_configured() -> bool:
    return bool(cfg.temporal_address)


def submit_review(
    repo: str,
    pr_number: int,
    mock: bool = False,
    output_dir: str = "reports",
    provider: str = "github",
) -> dict:
    """Sync entrypoint: starts the workflow and blocks for its result.
    Callers (webhook handlers, CLI) should only call this when
    is_configured() is True — otherwise call app.run_review() directly."""
    return asyncio.run(_submit_review_async(repo, pr_number, mock, output_dir, provider))


async def _submit_review_async(repo, pr_number, mock, output_dir, provider) -> dict:
    from temporalio.client import Client

    client = await Client.connect(cfg.temporal_address)
    handle = await client.start_workflow(
        "ReviewWorkflow",
        {
            "repo": repo,
            "pr_number": pr_number,
            "mock": mock,
            "output_dir": output_dir,
            "provider": provider,
        },
        id=f"review-{repo.replace('/', '_')}-{pr_number}",
        task_queue=cfg.temporal_task_queue,
    )
    return await handle.result()


def run_worker() -> None:
    """Long-running worker process — registers the workflow + activity and
    polls cfg.temporal_task_queue. Run this as a separate process/deployment
    alongside the webhook/CLI."""
    asyncio.run(_run_worker_async())


async def _run_worker_async() -> None:
    from temporalio.client import Client
    from temporalio.worker import Worker

    client = await Client.connect(cfg.temporal_address)
    worker = Worker(
        client,
        task_queue=cfg.temporal_task_queue,
        workflows=[ReviewWorkflow],
        activities=[run_review_activity],
    )
    print(f"[temporal] Worker listening on task queue '{cfg.temporal_task_queue}'")
    await worker.run()


# ── Workflow + activity definitions (temporalio decorators) ────────────────
# Import-guarded: temporalio is an optional dependency, so importing this
# module elsewhere (e.g. to call is_configured()) must not require it.
try:
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy

    @activity.defn
    async def run_review_activity(params: dict) -> dict:
        return await run_review_activity_impl(params)

    @workflow.defn
    class ReviewWorkflow:
        @workflow.run
        async def run(self, params: dict) -> dict:
            return await workflow.execute_activity(
                run_review_activity,
                params,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

except ImportError:
    ReviewWorkflow = None
    run_review_activity = None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        run_worker()
    else:
        print("Usage: python -m workflows.temporal_workflow worker")
