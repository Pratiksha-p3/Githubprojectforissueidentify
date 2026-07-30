"""
src/worker/celery_app.py

Celery application — the durable async queue that replaces the previous
implementation's in-process FastAPI BackgroundTasks (which dropped work
on a crash with no record and no retry). Redis is both broker and result
backend for now, per the "Celery+Redis instead of Kafka/SQS" infra
substitution documented in the rewrite plan.
"""
from __future__ import annotations

from celery import Celery

from src.core.config import settings

celery_app = Celery(
    "ai_code_review",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["src.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Don't ack a task until it actually finishes — a worker crash
    # mid-task redelivers the job instead of silently losing it, which is
    # the whole point of using a durable queue instead of BackgroundTasks.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
