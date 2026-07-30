"""
src/storage/postgres_store.py

Durable history for every ReviewResult — the seed of the later
event-sourcing log (Stage 14 concern), and what src/core/risk_scorer.py
and the dashboard (src/dashboard/) read from to compute trends.

The connection is injectable so tests can pass a fake connection/cursor
rather than needing a live Postgres server — same pattern as
src/storage/idempotency_store.py's injectable Redis client.
"""
from __future__ import annotations

import json

import psycopg

from src.core.config import settings
from src.core.models import ReviewResult

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS review_results (
    id SERIAL PRIMARY KEY,
    repo TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    critical_count INTEGER NOT NULL,
    total_findings INTEGER NOT NULL,
    summary TEXT NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL,
    findings_json TEXT NOT NULL
);
"""

_INSERT_SQL = """
INSERT INTO review_results
    (repo, commit_sha, status, critical_count, total_findings, summary, reviewed_at, findings_json)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_SELECT_HISTORY_SQL = """
SELECT repo, commit_sha, status, critical_count, total_findings, summary, reviewed_at
FROM review_results
WHERE repo = %s
ORDER BY reviewed_at DESC
LIMIT %s
"""


class PostgresStore:
    def __init__(self, conn: psycopg.Connection | None = None):
        self._conn = conn or psycopg.connect(settings.database_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def save_review(self, result: ReviewResult) -> None:
        findings_json = json.dumps([f.model_dump(mode="json") for f in result.findings])
        with self._conn.cursor() as cur:
            cur.execute(
                _INSERT_SQL,
                (
                    result.repo,
                    result.commit_sha,
                    result.status.value,
                    result.critical_count,
                    len(result.findings),
                    result.summary,
                    result.reviewed_at,
                    findings_json,
                ),
            )
        self._conn.commit()

    def get_history(self, repo: str, limit: int = 20) -> list[dict]:
        """Returns lightweight history rows (not full ReviewResult
        objects — risk scoring and the dashboard only need the summary
        fields, not every finding's full detail), most-recent first."""
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_HISTORY_SQL, (repo, limit))
            rows = cur.fetchall()
        return [
            {
                "repo": row[0],
                "commit_sha": row[1],
                "status": row[2],
                "critical_count": row[3],
                "total_findings": row[4],
                "summary": row[5],
                "reviewed_at": row[6],
            }
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
