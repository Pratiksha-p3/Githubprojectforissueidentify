"""
src/core/confidence.py

Single source of truth for "is this fix safe to apply without a human
looking at it first?" — every auto-apply/auto-commit path (Stage 4's
worker, Stage 12's fix-actions endpoint) must call this rather than
re-deriving its own confidence check, so tightening the bar happens in
one place, not N call sites that can drift out of sync.

Only ConfidenceTier.HIGH ever qualifies. HIGH is reserved for fixes that
are a verified fact about the code, not a heuristic judgment call — e.g.
"this import is never referenced anywhere in the file" or a literal
regex-anchored substitution for a known vulnerability shape. The
AST-based checkers introduced in this stage (dict-key, division-guard,
file-exists, constructor-param, http-timeout) generate a *reasonable*
fix, but which exact guard behavior is correct (raise vs. return None vs.
skip) is a judgment call about intent, not a certainty — so they're
MEDIUM by design, not HIGH, and are never auto-applied without review.

As of the current checker set (including hardcoded_secret_checker and
sql_injection_checker), NO checker or LLM finding source produces HIGH —
summarize_auto_fix_status() below will therefore always report 0
auto-fixed and every finding as requiring manual review. That isn't a
bug to chase down; it's this module's whole point. It becomes non-zero
the moment (if ever) a checker is deliberately built to that higher bar.
"""
from __future__ import annotations

from src.core.models import ConfidenceTier, Finding


def is_safe_to_auto_apply(finding: Finding) -> bool:
    return finding.confidence == ConfidenceTier.HIGH


def manual_review_reason(finding: Finding) -> str:
    """Why a human needs to look at this finding before any fix gets
    applied — driven by the same confidence tier is_safe_to_auto_apply()
    checks, so the two can never drift out of sync with each other."""
    if is_safe_to_auto_apply(finding):
        return ""
    if not finding.fix.strip():
        return "No fix was generated for this finding — needs manual investigation."
    if finding.confidence == ConfidenceTier.MEDIUM:
        return (
            "Confidence is MEDIUM — a reasonable checker-generated fix, but "
            "which exact guard behavior is correct (raise vs. return vs. "
            "skip) is a judgment call about intent, not a verified certainty."
        )
    return (
        "Confidence is LOW — free-form LLM output with no independent "
        "verification beyond basic grounding/validity checks."
    )


def summarize_auto_fix_status(findings: list[Finding]) -> dict:
    """Total auto-fixed vs. requiring-manual-review counts for a batch of
    findings, plus a per-finding reason for the ones needing a human —
    the "Total Issues Auto-Fixed" / "Total Issues Requiring Manual Review
    (Human-in-the-Loop)" / "Reason for Manual Intervention" reporting."""
    auto_fixed = [f for f in findings if is_safe_to_auto_apply(f)]
    manual = [f for f in findings if not is_safe_to_auto_apply(f)]
    return {
        "auto_fixed_count": len(auto_fixed),
        "manual_review_count": len(manual),
        "manual_review_details": [
            {
                "file": f.file,
                "line": f.line,
                "source": f.source,
                "reason": manual_review_reason(f),
            }
            for f in manual
        ],
    }
