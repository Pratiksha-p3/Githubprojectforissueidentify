"""
src/core/models.py

Domain types every later stage builds on. Nothing here calls an LLM, runs a
checker, or talks to GitHub — this is the shared vocabulary those stages
will use, defined first so it can't be worked around later.

ReviewStatus exists specifically so "the review didn't actually run" can
never be represented the same way as "the review ran and found nothing" —
that conflation was a real, previously-shipped bug: a rate-limited/failed
LLM call produced zero findings, which is indistinguishable from a clean
pass unless the status itself says otherwise. COMPLETED is the only status
a gate is ever allowed to treat as approvable.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class ConfidenceTier(StrEnum):
    """How much to trust a Finding's `fix`, from safest to least safe.

    HIGH  — deterministic rule-based substitution for a known, well-defined
            bug shape (e.g. an AST-verified fact). The only tier ever safe
            to auto-apply without a human looking at it.
    MEDIUM — a checker-generated fix being reused, or an LLM fix that passed
            grounding/validity checks but wasn't independently re-derived.
    LOW   — free-form LLM output with no independent verification beyond
            basic shape/grounding checks. Always requires human review.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(StrEnum):
    """Whether a review actually completed — never inferred from finding
    count alone. A COMPLETED review with zero findings is a clean pass;
    a DEGRADED or FAILED review with zero findings is unknown, not clean,
    and must never be treated as approvable by a gate."""

    COMPLETED = "completed"  # every configured check ran to completion
    DEGRADED = "degraded"    # some checks ran (e.g. deterministic), others
                             # didn't (e.g. LLM unavailable/rate-limited)
    FAILED = "failed"        # couldn't produce a usable result at all


class Finding(BaseModel):
    file: str
    line: int = Field(ge=0)
    category: str
    severity: Severity
    message: str
    bad_code: str = ""
    fix: str = ""
    confidence: ConfidenceTier = ConfidenceTier.LOW
    source: str = "unknown"  # e.g. "dict_key_checker", "llm_supplement", "semgrep"


class ReviewResult(BaseModel):
    repo: str
    commit_sha: str
    status: ReviewStatus
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def is_approvable(self) -> bool:
        """Only a COMPLETED review with no critical findings is approvable.
        DEGRADED/FAILED are never approvable regardless of finding count —
        see ReviewStatus's docstring for why."""
        return self.status == ReviewStatus.COMPLETED and self.critical_count == 0
