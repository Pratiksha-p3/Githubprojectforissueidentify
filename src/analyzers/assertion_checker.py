"""
src/analyzers/assertion_checker.py

Detects `assert <literal>` where the literal's truth value is
statically knowable and always falsy -- e.g. `assert False`, `assert
0`, `assert None`, `assert ""`, `assert []` -- a guaranteed
AssertionError every time that line runs. (Python's `-O` flag strips
asserts entirely at compile time, but that's a deployment choice this
project has no visibility into, not a reason to skip the finding.)

Only reasons about ast.Constant literals plus the empty-collection
literal forms, whose truthiness is unambiguous -- never a Name, Call,
Compare, or BoolOp, since evaluating those would require the same real
runtime information this project deliberately avoids inferring
elsewhere. A non-empty tuple as the test (the classic `assert (x,
"msg")` typo, which is always truthy and so the OPPOSITE bug --
silently disables the assertion) is a different bug and out of scope
here.

No fix is generated: whether the assertion is dead code that should be
deleted, or the condition itself was written wrong, isn't derivable
from the literal alone.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity


def _is_always_falsy_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return not node.value
    if isinstance(node, ast.List):
        return len(node.elts) == 0
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    if isinstance(node, ast.Tuple):
        return len(node.elts) == 0
    return False


def detect_guaranteed_assertion_failures(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        if not _is_always_falsy_literal(node.test):
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=(
                    "This assert's condition is a literal that's always falsy "
                    "— guaranteed AssertionError every time this line runs, "
                    "not just a possible one."
                ),
                bad_code=lines[node.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="assertion_checker",
            )
        )

    return findings
