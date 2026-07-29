"""
src/core/grounding.py

A Finding's `bad_code` is a claim: "this is what the current code at this
line actually looks like." When that claim comes from an LLM (Stage 2
onward), it can be wrong — invented, paraphrased, or anchored to the wrong
line — and if left unchecked, that fabricated content ends up quoted in a
PR comment or worse, used as the basis for a fix. This module is the single
place that claim gets checked against reality, so no later stage can add a
new finding source and forget to verify it — the analyzer registry
(src/analyzers/registry.py) calls this on every finding before it leaves
the analysis layer, deterministic checkers included.

Deterministic checkers technically always produce a grounded bad_code
(they read it straight from the AST), but running the same check on them
costs nothing and means this function has exactly one code path to trust,
not "trust checkers, verify LLM output" as two different policies.
"""
from __future__ import annotations

from src.core.models import Finding


def is_grounded(finding: Finding, source_text: str, window: int = 2) -> bool:
    """
    True if `finding.bad_code` actually appears, whitespace-normalized,
    within `window` lines of `finding.line` (1-indexed) in `source_text`.
    A small window tolerates being off by a line or two while still
    rejecting fabricated or misattributed content. An empty `bad_code`
    makes no claim about the code, so there's nothing to verify.
    """
    target = " ".join(finding.bad_code.split())
    if not target:
        return True

    lines = source_text.splitlines()
    if finding.line < 1:
        return False

    lo = max(0, finding.line - 1 - window)
    hi = min(len(lines), finding.line - 1 + window + 1)
    return any(target in " ".join(candidate.split()) for candidate in lines[lo:hi])
