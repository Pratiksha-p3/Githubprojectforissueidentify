"""
src/core/pr_gate.py

Decision logic for whether a PR can be approved, built on ReviewResult's
is_approvable property (src/core/models.py). Only COMPLETED with no
critical findings is ever APPROVE — DEGRADED and FAILED always come back
REVIEW_REQUIRED regardless of finding count, and can never be silently
treated as a clean pass. This directly fixes a real, previously-shipped
bug: a rate-limited LLM call producing zero findings looked identical to
a genuinely clean review and was auto-approved.
"""
from __future__ import annotations

from enum import StrEnum

from src.core.models import ReviewResult, ReviewStatus


class GateDecision(StrEnum):
    APPROVE = "approve"
    BLOCK = "block"
    REVIEW_REQUIRED = "review_required"


def decide(result: ReviewResult) -> GateDecision:
    if result.status != ReviewStatus.COMPLETED:
        return GateDecision.REVIEW_REQUIRED
    if result.critical_count > 0:
        return GateDecision.BLOCK
    return GateDecision.APPROVE


def gate_reason(result: ReviewResult) -> str:
    decision = decide(result)

    if decision == GateDecision.REVIEW_REQUIRED:
        return (
            f"Review did not complete ({result.status.value}) — "
            f"{result.summary or 'see logs for details'} Re-run once the "
            f"underlying issue clears; do not treat this as a clean pass."
        )
    if decision == GateDecision.BLOCK:
        plural = "s" if result.critical_count != 1 else ""
        return f"{result.critical_count} critical finding{plural} must be resolved before merge."
    return "No critical issues — approved."
