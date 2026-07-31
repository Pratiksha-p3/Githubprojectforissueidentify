"""
src/analyzers/sql_injection_checker.py

Detects SQL built via string interpolation/concatenation and passed to a
*.execute(...) call -- the classic SQL injection shape:

    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

instead of a parameterized query
(cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))).

Two call shapes are covered, since the unsafe string-building often
happens on its own line rather than inline in the execute() call:
  1. execute(<unsafe-expression>) directly -- the argument itself is an
     f-string or a %/+ built string.
  2. execute(some_name) where `some_name` was assigned an unsafe
     expression anywhere earlier in the same function (or module scope,
     for module-level code) -- covers `query = f"..."` followed by
     `cursor.execute(query)`, which is the more common real-world shape.
     This is a heuristic, not real dataflow analysis (it doesn't check
     that the assignment actually happens *before* the execute() call in
     control-flow order) -- consistent with how e.g. dict_key_checker's
     _already_guarded() also scans the whole enclosing function rather
     than tracking precise execution order.

Matches ANY object's .execute() method, not just a verified DB cursor --
narrowing further would need type inference this project doesn't do, and
in practice ".execute(<unsafe string>)" is specific enough (the same
scope bandit's and semgrep's default SQL-injection rules settle on) that
the rare false positive on an unrelated .execute() method is worth it
for not missing a real injection.

No fix is generated: the correct parameterized rewrite depends on the
target DB driver's placeholder style (?, %s, :name, ...) and how many
values are interpolated, neither of which is reliably derivable from the
AST alone. Detection, not auto-fix, is this checker's job here.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import build_parent_map, owning_function
from src.core.models import ConfidenceTier, Finding, Severity


def _is_unsafe_query_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return True
    return False


def _assigned_unsafely_in_scope(scope: ast.AST, name: str) -> bool:
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        if _is_unsafe_query_expr(node.value):
            return True
    return False


def detect_sql_injection(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        if not node.args:
            continue

        arg = node.args[0]
        unsafe = _is_unsafe_query_expr(arg)
        if not unsafe and isinstance(arg, ast.Name):
            scope = owning_function(parent_map, node) or tree
            unsafe = _assigned_unsafely_in_scope(scope, arg.id)
        if not unsafe:
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        original_line = lines[node.lineno - 1]
        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="security",
                severity=Severity.CRITICAL,
                message=(
                    "SQL query is built via string interpolation/concatenation "
                    "and passed to .execute(...) — vulnerable to SQL injection. "
                    'Use a parameterized query instead (e.g. cursor.execute("... '
                    'WHERE id = ?", (user_id,))).'
                ),
                bad_code=original_line.strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="sql_injection_checker",
            )
        )

    return findings
