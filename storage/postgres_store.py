"""
storage/postgres_store.py

Optional Postgres persistence for review reports. The app already writes
every report to reports/*.json — that file write always happens and is
never removed. This module adds a queryable copy in Postgres (so a
dashboard/BI tool can do `SELECT * FROM reviews WHERE repo = ...` instead
of grepping JSON files) when DATABASE_URL is configured.

Inert without DATABASE_URL: is_configured() returns False and save_report()
is a no-op, so the app's behavior is unchanged until this is set up.

Install: pip install sqlalchemy psycopg2-binary
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from config import cfg

_engine = None
_Session = None


def is_configured() -> bool:
    return bool(cfg.database_url)


def save_report(report: dict) -> bool:
    """Persists one review report as a row. Returns False (without raising)
    if Postgres isn't configured or the write fails — callers should treat
    this purely as a nice-to-have mirror of the JSON file, never a
    dependency for the review pipeline to complete."""
    if not is_configured():
        return False

    try:
        session = _get_session()
        row = ReviewRecord(
            repo=report.get("repo", ""),
            pr_number=report.get("pr_number", 0),
            head_sha=report.get("head_sha", ""),
            pr_title=report.get("pr_title", ""),
            author=(report.get("skill_profile") or {}).get("author", ""),
            overall_score=float(report.get("overall_score", 0.0)),
            approved=bool(report.get("approved", False)),
            total_findings=int(report.get("total_findings", 0)),
            critical_count=int(report.get("critical_count", 0)),
            warning_count=int(report.get("warning_count", 0)),
            reviewed_at=report.get("reviewed_at", datetime.now(timezone.utc).isoformat()),
            report_json=json.dumps(report),
        )
        session.add(row)
        session.commit()
        session.close()
        print(f"[postgres] Saved review {report.get('repo')}#{report.get('pr_number')}")
        return True
    except Exception as e:
        print(f"[postgres] Failed to save report: {e}")
        return False


def get_reports(repo: str = None, limit: int = 50) -> list[dict]:
    """Returns recent reports, optionally filtered by repo. Empty list if
    Postgres isn't configured — callers should fall back to reading the
    reports/*.json files instead."""
    if not is_configured():
        return []
    try:
        session = _get_session()
        query = session.query(ReviewRecord).order_by(ReviewRecord.id.desc())
        if repo:
            query = query.filter(ReviewRecord.repo == repo)
        rows = query.limit(limit).all()
        session.close()
        return [json.loads(r.report_json) for r in rows]
    except Exception as e:
        print(f"[postgres] Failed to fetch reports: {e}")
        return []


# ── Internal ──────────────────────────────────────────────────────────────

def _get_session():
    global _engine, _Session
    if _Session is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        _engine = create_engine(cfg.database_url, pool_pre_ping=True)
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine)
    return _Session()


try:
    from sqlalchemy import Column, Integer, String, Float, Boolean, Text
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class ReviewRecord(Base):
        __tablename__ = "reviews"

        id = Column(Integer, primary_key=True, autoincrement=True)
        repo = Column(String(255), index=True)
        pr_number = Column(Integer, index=True)
        head_sha = Column(String(64))
        pr_title = Column(String(500))
        author = Column(String(255), index=True)
        overall_score = Column(Float)
        approved = Column(Boolean)
        total_findings = Column(Integer)
        critical_count = Column(Integer)
        warning_count = Column(Integer)
        reviewed_at = Column(String(64))
        report_json = Column(Text)

except ImportError:
    Base = None
    ReviewRecord = None
