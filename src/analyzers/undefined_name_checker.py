"""
src/analyzers/undefined_name_checker.py

Detects a name referenced with no binding anywhere it could resolve
from (the enclosing function, an outer enclosing function, module
scope, or Python's builtins) -- raises an exception the moment that code
actually executes.

Every other checker in this package works by matching a local AST
*shape* (a call, an assignment, a subscript) -- detecting this correctly
needs something fundamentally different: a real symbol table, walking
out through every enclosing scope and accounting for closures,
comprehension scoping, `global`/`nonlocal`, conditional imports, star
imports, and more. Hand-rolling that from scratch is how you get
something with worse edge-case coverage than a tool that already solves
it -- this wraps pyflakes's Checker (the same analysis `ruff` itself
uses internally for undefined-name detection) rather than
reimplementing scope resolution.

Only pyflakes.messages.UndefinedName is surfaced here -- pyflakes
reports many other categories (unused imports, unused variables,
redefinitions, ...) that are legitimate but out of scope for a checker
specifically about this.

pyflakes.messages.UndefinedName covers TWO genuinely different runtime
exceptions, and reporting both as "NameError" is a real inaccuracy this
checker used to have, caught live: `def f(): print(x); x = 1` raises
UnboundLocalError, not NameError -- because `x` is assigned SOMEWHERE in
the function (just later), Python's compiler treats it as local for the
function's ENTIRE body, so the earlier read fails to find a value for a
name it already knows is local, a different failure than a name with no
local binding at all. _is_assigned_in_own_scope() below re-checks each
flagged name against its own enclosing function (deliberately NOT
descending into a nested function/class def, which would be a separate
scope) to report the exception Python would actually raise, not just
"undefined" for both cases alike.

No fix is generated: the correct resolution (define the name earlier,
fix a typo, add a missing import, adjust scope, reorder statements)
depends entirely on what was actually meant, which isn't recoverable
from the reference alone -- same "detection, not auto-fix" stance
src/analyzers/sql_injection_checker.py already takes for a bug class
whose safe rewrite depends on context this project doesn't have.
"""
from __future__ import annotations

import ast

from pyflakes.checker import Checker
from pyflakes.messages import UndefinedName

from src.core.models import ConfidenceTier, Finding, Severity

_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _enclosing_function(
    tree: ast.AST, lineno: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The innermost function whose line range contains `lineno`, found
    by range containment rather than parent-pointer lookup -- pyflakes's
    messages carry only (lineno, col), not the actual ast.Name node."""
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if node.lineno <= lineno <= end and (best is None or node.lineno > best.lineno):
            best = node
    return best


def _is_assigned_in_own_scope(func: ast.AST, name: str) -> bool:
    """True if `name` is bound (Store context) somewhere directly in
    `func`'s own body -- deliberately NOT inside a nested function/
    lambda/class def, which introduces its own separate scope instead of
    extending this one."""

    def visit(node: ast.AST, is_root: bool) -> bool:
        if not is_root and isinstance(node, _SCOPE_BOUNDARY):
            return False
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == name:
            return True
        return any(visit(child, False) for child in ast.iter_child_nodes(node))

    return visit(func, True)


def detect_undefined_names(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    lines = code.splitlines()
    findings: list[Finding] = []
    for msg in Checker(tree, filename=filename).messages:
        if not isinstance(msg, UndefinedName):
            continue
        lineno = msg.lineno
        bad_code = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""

        name = msg.message_args[0] if msg.message_args else ""
        func = _enclosing_function(tree, lineno)
        if func is not None and _is_assigned_in_own_scope(func, name):
            exception_note = (
                f"raises UnboundLocalError: '{name}' is assigned later in this "
                f"same function, which makes it a local variable for the "
                f"function's entire body -- referencing it before that "
                f"assignment actually runs fails, even though the name isn't "
                f"truly undefined."
            )
        else:
            exception_note = "raises NameError the moment this code actually runs."

        findings.append(
            Finding(
                file=filename,
                line=lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=f"{msg.message % msg.message_args} — {exception_note}",
                bad_code=bad_code,
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="undefined_name_checker",
            )
        )

    return findings
