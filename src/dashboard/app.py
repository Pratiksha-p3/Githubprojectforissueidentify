"""
src/dashboard/app.py

Read-only dashboard over review history: per-repo risk score/trend
(src/core/risk_scorer.py) plus JSON/PDF export (src/dashboard/export.py).
FastAPI, consistent with src/api/webhook.py's pattern.

The store is provided via FastAPI's dependency injection (get_store)
rather than instantiated directly in each route, so tests can override
it with a fake store instead of needing a live Postgres server — same
principle as every other injectable-client pattern in this project
(IdempotencyStore, DLQStore, GitHubClient, ...).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Response

from src.core.risk_scorer import compute_risk_score
from src.dashboard.export import export_json, export_pdf
from src.storage.postgres_store import PostgresStore

app = FastAPI(title="AI Code Review Dashboard")


def get_store() -> PostgresStore:
    return PostgresStore()


def _risk_dict(history: list[dict]) -> dict:
    risk = compute_risk_score(history)
    return {
        "score": risk.score,
        "trend": risk.trend,
        "reviews_considered": risk.reviews_considered,
    }


@app.get("/api/repos/{repo:path}/risk")
def repo_risk(repo: str, store: PostgresStore = Depends(get_store)) -> dict:
    history = store.get_history(repo, limit=20)
    return {"repo": repo, **_risk_dict(history)}


@app.get("/api/repos/{repo:path}/history")
def repo_history(repo: str, limit: int = 20, store: PostgresStore = Depends(get_store)) -> dict:
    history = store.get_history(repo, limit=limit)
    return {"repo": repo, "history": history}


@app.get("/api/repos/{repo:path}/export/json")
def export_repo_json(repo: str, store: PostgresStore = Depends(get_store)) -> Response:
    history = store.get_history(repo, limit=100)
    content = export_json(repo, history, _risk_dict(history))
    return Response(content=content, media_type="application/json")


@app.get("/api/repos/{repo:path}/export/pdf")
def export_repo_pdf(repo: str, store: PostgresStore = Depends(get_store)) -> Response:
    history = store.get_history(repo, limit=100)
    pdf_bytes = export_pdf(repo, history, _risk_dict(history))
    return Response(content=pdf_bytes, media_type="application/pdf")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
