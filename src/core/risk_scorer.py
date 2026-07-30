"""
src/core/risk_scorer.py

Aggregates a repo's review history (src/storage/postgres_store.py's
get_history()) into a single 0-100 risk score and a trend direction —
what the dashboard displays per repo. Weighted toward recent reviews, so
a repo that fixed its issues last week is lower-risk today than its
all-time average would suggest.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskScore:
    repo: str
    score: float  # 0 (no risk) - 100 (highest risk)
    trend: str  # "improving" | "worsening" | "stable"
    reviews_considered: int


def compute_risk_score(history: list[dict]) -> RiskScore:
    """
    `history` is PostgresStore.get_history()'s output, most-recent-first.
    Empty history is zero risk (nothing to be risky about yet), not an
    error condition.
    """
    if not history:
        return RiskScore(repo="", score=0.0, trend="stable", reviews_considered=0)

    repo = history[0]["repo"]
    n = len(history)

    # Weight recent reviews more heavily — index 0 (most recent) gets the
    # highest weight, decaying linearly to the oldest review considered.
    weights = [n - i for i in range(n)]
    total_weight = sum(weights)
    weighted_risk = sum(w * _review_risk(row) for w, row in zip(weights, history)) / total_weight

    return RiskScore(
        repo=repo,
        score=round(weighted_risk, 1),
        trend=_compute_trend(history),
        reviews_considered=n,
    )


def _review_risk(row: dict) -> float:
    """0-100 risk contribution from a single review. A DEGRADED/FAILED
    review is moderately risky on its own — status COMPLETED with a
    critical finding is a known problem, but an incomplete review means
    genuinely unknown, and unknown is never treated as safe."""
    base = 0.0 if row["status"] == "completed" else 30.0
    critical_risk = min(row["critical_count"] * 20, 70)
    other_findings = max(row["total_findings"] - row["critical_count"], 0)
    other_findings_risk = min(other_findings * 3, 20)
    return min(base + critical_risk + other_findings_risk, 100.0)


def _compute_trend(history: list[dict]) -> str:
    if len(history) < 2:
        return "stable"

    midpoint = len(history) // 2 or 1
    recent_half = history[:midpoint]
    older_half = history[midpoint:]
    if not older_half:
        return "stable"

    recent_avg = sum(_review_risk(r) for r in recent_half) / len(recent_half)
    older_avg = sum(_review_risk(r) for r in older_half) / len(older_half)

    if recent_avg < older_avg - 5:
        return "improving"
    if recent_avg > older_avg + 5:
        return "worsening"
    return "stable"
