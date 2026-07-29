"""
src/analyzers/division_guard_checker.py

Detects `x / <param>` where the denominator is a bare function parameter
(its value isn't knowable statically — the caller could pass zero) and
there's no visible guard against zero anywhere in the function. Fix
inserts an explicit zero-check immediately before the dividing statement.

Deliberately narrow scope, matching this package's low-false-positive
convention:
  - Only bare-Name denominators that are function PARAMETERS — a local
    variable assigned from a literal has knowable contents; a call
    expression like `len(x)` is a different, harder-to-guard shape and
    is left alone rather than guessed at.
  - Skipped if the function already has an `if <name> == 0`/`!= 0`/
    truthy-check on that name, or a surrounding `try/except` catching
    `ZeroDivisionError`/`Exception`.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import (
    build_parent_map,
    exception_names,
    line_indent,
    owning_function,
    param_names,
)
from src.core.models import ConfidenceTier, Finding, Severity


def _already_guarded(func: ast.AST, denom_name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Compare):
                if (
                    isinstance(test.left, ast.Name)
                    and test.left.id == denom_name
                    and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in test.ops)
                ):
                    return True
            if isinstance(test, ast.Name) and test.id == denom_name:
                return True
            if (
                isinstance(test, ast.UnaryOp)
                and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Name)
                and test.operand.id == denom_name
            ):
                return True
        if isinstance(node, ast.Try):
            names = [n for h in node.handlers for n in exception_names(h.type)]
            if any(n in ("ZeroDivisionError", "Exception") for n in names):
                return True
    return False


def _owning_statement_line(parent_map: dict[int, ast.AST], node: ast.AST) -> int:
    """Walk up to the nearest enclosing statement so the fix guard is
    inserted before a whole statement, not spliced mid-expression."""
    current: ast.AST = node
    while current is not None and not isinstance(current, ast.stmt):
        parent = parent_map.get(id(current))
        if parent is None:
            break
        current = parent
    return getattr(current, "lineno", getattr(node, "lineno", 0))


def detect_unguarded_division(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = []
    seen_lines: set[tuple[str, int]] = set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        if not isinstance(node.right, ast.Name):
            continue

        func = owning_function(parent_map, node)
        if func is None or node.right.id not in param_names(func):
            continue
        if _already_guarded(func, node.right.id):
            continue

        stmt_line = _owning_statement_line(parent_map, node)
        if not (0 < stmt_line <= len(lines)):
            continue
        if (filename, stmt_line) in seen_lines:
            continue
        seen_lines.add((filename, stmt_line))

        stmt_text = lines[stmt_line - 1]
        indent = line_indent(stmt_text)
        denom = node.right.id

        guard = (
            f'{indent}if {denom} == 0:\n'
            f'{indent}    raise ZeroDivisionError('
            f'f"\'{denom}\' is zero")'
        )
        fix_code = f"{guard}\n{stmt_text}"

        findings.append(
            Finding(
                file=filename,
                line=stmt_line,
                category="runtime",
                severity=Severity.WARNING,
                message=(
                    f"Division by parameter '{denom}' with no zero-check — "
                    f"raises ZeroDivisionError if the caller passes 0."
                ),
                bad_code=stmt_text.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="division_guard_checker",
            )
        )

    return findings
