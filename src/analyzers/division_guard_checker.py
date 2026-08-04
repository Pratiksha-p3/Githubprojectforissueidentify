"""
src/analyzers/division_guard_checker.py

Detects two shapes of "denominator isn't knowable statically, and
there's no zero-check anywhere in the function": `x / <param>` (a bare
function parameter as the divisor) and `x / len(<param>)` (dividing by
a parameter's LENGTH -- the far more common real shape in practice,
e.g. an average: `total / len(numbers)`, which raises ZeroDivisionError
for an empty list). Fix inserts an explicit zero-check immediately
before the dividing statement.

Deliberately narrow scope, matching this package's low-false-positive
convention:
  - Only a bare-Name PARAMETER, or `len()` of one -- a local variable
    assigned from a literal has knowable contents; any other call
    expression (`compute_count(x)`, etc.) is a different, harder-to-
    guard shape and is left alone rather than guessed at.
  - Skipped if the function already has an `if <name> == 0`/`!= 0`/
    truthy-check on the denominator itself, `if len(<name>) == 0`/`> 0`/
    truthy-check on the LENGTH shape, or a surrounding `try/except`
    catching `ZeroDivisionError`/`Exception`. For the len() shape, a
    plain `if <param>:`/`if not <param>:` also counts -- an empty
    sequence is falsy, so that's an equally valid guard against the
    same zero-length condition.
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


def _is_len_of_name(node: ast.expr, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == name
    )


def _truthy_guard_on(test: ast.expr, name: str) -> bool:
    if isinstance(test, ast.Name) and test.id == name:
        return True
    return bool(
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Name)
        and test.operand.id == name
    )


def _already_guarded(func: ast.AST, param_name: str, *, is_len_shape: bool) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Compare) and any(
                isinstance(op, (ast.Eq, ast.NotEq, ast.Gt, ast.GtE)) for op in test.ops
            ):
                left_is_param = isinstance(test.left, ast.Name) and test.left.id == param_name
                left_is_len = _is_len_of_name(test.left, param_name)
                if left_is_param or (is_len_shape and left_is_len):
                    return True
            if _truthy_guard_on(test, param_name):
                return True  # an empty sequence is falsy -- guards the len()==0 shape too
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

        func = owning_function(parent_map, node)
        if func is None:
            continue
        params = param_names(func)

        is_len_shape = False
        if isinstance(node.right, ast.Name) and node.right.id in params:
            param_name = node.right.id
        elif isinstance(node.right, ast.Call):
            call_arg = node.right.args[0] if node.right.args else None
            if (
                isinstance(node.right.func, ast.Name)
                and node.right.func.id == "len"
                and isinstance(call_arg, ast.Name)
                and call_arg.id in params
            ):
                param_name = call_arg.id
                is_len_shape = True
            else:
                continue
        else:
            continue

        if _already_guarded(func, param_name, is_len_shape=is_len_shape):
            continue

        stmt_line = _owning_statement_line(parent_map, node)
        if not (0 < stmt_line <= len(lines)):
            continue
        if (filename, stmt_line) in seen_lines:
            continue
        seen_lines.add((filename, stmt_line))

        stmt_text = lines[stmt_line - 1]
        indent = line_indent(stmt_text)
        denom_expr = f"len({param_name})" if is_len_shape else param_name

        guard = (
            f'{indent}if {denom_expr} == 0:\n'
            f'{indent}    raise ZeroDivisionError('
            f'f"\'{param_name}\' is {"empty" if is_len_shape else "zero"}")'
        )
        fix_code = f"{guard}\n{stmt_text}"

        message = (
            f"Division by len('{param_name}') with no empty-check — raises "
            f"ZeroDivisionError if the caller passes an empty sequence."
            if is_len_shape
            else (
                f"Division by parameter '{param_name}' with no zero-check — "
                f"raises ZeroDivisionError if the caller passes 0."
            )
        )

        findings.append(
            Finding(
                file=filename,
                line=stmt_line,
                category="runtime",
                severity=Severity.WARNING,
                message=message,
                bad_code=stmt_text.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="division_guard_checker",
            )
        )

    return findings
