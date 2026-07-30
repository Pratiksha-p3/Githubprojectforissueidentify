"""
src/core/grounding.py

The shared trust layer every Finding passes through before it's allowed to
leave the analysis layer — deterministic checker or LLM supplement alike.
Two independent claims get checked:

  is_grounded  — "this is what the current code at this line actually
                 looks like" (bad_code). When that claim comes from an
                 LLM, it can be wrong: invented, paraphrased, or anchored
                 to the wrong line. Left unchecked, fabricated content
                 ends up quoted in a PR comment or used as the basis for
                 a fix.
  is_valid_fix — "here's a replacement that will work" (fix). A fix that
                 doesn't even parse as valid Python is never safe to
                 show or auto-apply, regardless of whether the diagnosis
                 was right.

is_trustworthy() combines both and is what callers (src/analyzers/
registry.py, src/core/orchestrator.py) should actually call — so no
future finding source can add a new one and forget one of the two checks.

Deterministic checkers technically always produce a grounded bad_code and
a parseable fix (both come straight from the AST), but running the same
checks on them costs nothing and means there's exactly one code path to
trust, not "trust checkers, verify LLM output" as two different policies.
"""
from __future__ import annotations

import ast

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


def is_valid_fix(fix: str) -> bool:
    """True if `fix` parses as valid standalone Python. Wraps in a dummy
    block first if it looks pre-indented, since a fix's own indentation
    reflects where it belongs in the file, not module level. An empty
    fix proposes nothing, so there's nothing to invalidate."""
    if not fix.strip():
        return True
    first_line = fix.splitlines()[0]
    try:
        wrapped = f"if True:\n{fix}" if first_line[:1] in (" ", "\t") else fix
        ast.parse(wrapped)
        return True
    except SyntaxError:
        return False


def is_trustworthy(finding: Finding, source_text: str) -> bool:
    return is_grounded(finding, source_text) and is_valid_fix(finding.fix)
