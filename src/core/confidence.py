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
"""
from __future__ import annotations

from src.core.models import ConfidenceTier, Finding


def is_safe_to_auto_apply(finding: Finding) -> bool:
    return finding.confidence == ConfidenceTier.HIGH
