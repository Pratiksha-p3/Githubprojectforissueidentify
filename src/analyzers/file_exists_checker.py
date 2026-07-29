"""
src/analyzers/file_exists_checker.py

Detects `open(path, ...)` calls with no guard against the file not
existing — raises FileNotFoundError at runtime if the caller passes a
missing path. Fix wraps the call's enclosing statement in a try/except.

Scope: only flags `open(...)` where the first argument is a Name (a
variable/parameter — unknowable statically), and skips it if already
inside a `try/except` catching `FileNotFoundError`/`OSError`/`Exception`,
or guarded by a preceding `if os.path.exists(...)` / `if os.path.isfile(...)`
check on the same name anywhere in the enclosing function.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import (
    build_parent_map,
    exception_names,
    line_indent,
    owning_function,
)
from src.core.models import ConfidenceTier, Finding, Severity


def _already_guarded(func: ast.AST, path_name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("exists", "isfile") and any(
                isinstance(a, ast.Name) and a.id == path_name for a in node.args
            ):
                return True
        if isinstance(node, ast.Try):
            names = [n for h in node.handlers for n in exception_names(h.type)]
            if any(n in ("FileNotFoundError", "OSError", "Exception") for n in names):
                return True
    return False


def _owning_statement(parent_map: dict[int, ast.AST], node: ast.AST) -> ast.stmt | None:
    current: ast.AST = node
    while current is not None and not isinstance(current, ast.stmt):
        parent = parent_map.get(id(current))
        if parent is None:
            return None
        current = parent
    return current if isinstance(current, ast.stmt) else None


def detect_unguarded_file_open(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = []
    seen_lines: set[int] = set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "open" or not node.args:
            continue
        path_arg = node.args[0]
        if not isinstance(path_arg, ast.Name):
            continue

        func = owning_function(parent_map, node)
        if func is None:
            continue
        if _already_guarded(func, path_arg.id):
            continue

        stmt = _owning_statement(parent_map, node)
        if stmt is None or not (0 < stmt.lineno <= len(lines)):
            continue
        if stmt.lineno in seen_lines:
            continue
        seen_lines.add(stmt.lineno)

        stmt_text = lines[stmt.lineno - 1]
        indent = line_indent(stmt_text)
        inner_indent = indent + "    "

        fix_code = (
            f"{indent}try:\n"
            f"{inner_indent}{stmt_text.strip()}\n"
            f"{indent}except FileNotFoundError:\n"
            f'{inner_indent}raise FileNotFoundError(f"File not found: {{{path_arg.id}}}")'
        )

        findings.append(
            Finding(
                file=filename,
                line=stmt.lineno,
                category="runtime",
                severity=Severity.WARNING,
                message=(
                    f"open({path_arg.id}, ...) has no guard against a missing "
                    f"file — raises FileNotFoundError if the path doesn't exist."
                ),
                bad_code=stmt_text.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="file_exists_checker",
            )
        )

    return findings
