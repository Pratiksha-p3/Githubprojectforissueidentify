"""
src/storage/decision_log.py

Persists every accept/reject decision made on a suggested fix — the
"historical accept/reject learning loop" requirement. Recorded so future
prompt tuning can be informed by real data (which checkers/agents get
their fixes accepted vs. rejected, and how often) — automated retuning
itself stays a manual/research step given no real eval-drift
infrastructure exists yet (that's Stage 14 territory), but the data to
eventually do that is captured starting here rather than never.
"""
from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from src.core.config import settings

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fix_decisions (
    id SERIAL PRIMARY KEY,
    repo TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    finding_source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    decision TEXT NOT NULL,
    actor TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL
);
"""

_INSERT_SQL = """
INSERT INTO fix_decisions
    (repo, commit_sha, file, line, finding_source, confidence, decision, actor, decided_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SELECT_SQL = """
SELECT repo, commit_sha, file, line, finding_source, confidence, decision, actor, decided_at
FROM fix_decisions
WHERE finding_source = %s
ORDER BY decided_at DESC
LIMIT %s
"""

_ACCEPTANCE_RATE_SQL = """
SELECT
    finding_source,
    COUNT(*) FILTER (WHERE decision = 'accepted') AS accepted,
    COUNT(*) AS total
FROM fix_decisions
GROUP BY finding_source
"""


class DecisionLog:
    def __init__(self, conn: psycopg.Connection | None = None):
        self._conn = conn or psycopg.connect(settings.database_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def record_decision(
        self,
        *,
        repo: str,
        commit_sha: str,
        file: str,
        line: int,
        finding_source: str,
        confidence: str,
        decision: str,
        actor: str,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                _INSERT_SQL,
                (
                    repo,
                    commit_sha,
                    file,
                    line,
                    finding_source,
                    confidence,
                    decision,
                    actor,
                    datetime.now(UTC),
                ),
            )
        self._conn.commit()

    def history_for_source(self, finding_source: str, limit: int = 50) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_SQL, (finding_source, limit))
            rows = cur.fetchall()
        return [
            {
                "repo": row[0],
                "commit_sha": row[1],
                "file": row[2],
                "line": row[3],
                "finding_source": row[4],
                "confidence": row[5],
                "decision": row[6],
                "actor": row[7],
                "decided_at": row[8],
            }
            for row in rows
        ]

    def acceptance_rates(self) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(_ACCEPTANCE_RATE_SQL)
            rows = cur.fetchall()
        return [
            {
                "finding_source": row[0],
                "accepted": row[1],
                "total": row[2],
                "acceptance_rate": round(row[1] / row[2], 3) if row[2] else 0.0,
            }
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
