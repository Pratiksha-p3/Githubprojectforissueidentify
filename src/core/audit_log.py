"""
src/core/audit_log.py

Append-only audit trail of every review action (who/what/when) — the
"every review action logged for compliance" requirement. Persisted to
Postgres via the same connection-injection pattern as
src/storage/postgres_store.py, since audit records share the same
durability need as review history.
"""
from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from src.core.config import settings

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    repo TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    detail TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);
"""

_INSERT_SQL = """
INSERT INTO audit_log (actor, action, repo, commit_sha, detail, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s)
"""

_SELECT_SQL = """
SELECT actor, action, repo, commit_sha, detail, occurred_at
FROM audit_log
WHERE repo = %s
ORDER BY occurred_at DESC
LIMIT %s
"""


class AuditLog:
    def __init__(self, conn: psycopg.Connection | None = None):
        self._conn = conn or psycopg.connect(settings.database_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def record(
        self, *, actor: str, action: str, repo: str, commit_sha: str, detail: str = ""
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(_INSERT_SQL, (actor, action, repo, commit_sha, detail, datetime.now(UTC)))
        self._conn.commit()

    def history_for_repo(self, repo: str, limit: int = 50) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_SQL, (repo, limit))
            rows = cur.fetchall()
        return [
            {
                "actor": row[0],
                "action": row[1],
                "repo": row[2],
                "commit_sha": row[3],
                "detail": row[4],
                "occurred_at": row[5],
            }
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
