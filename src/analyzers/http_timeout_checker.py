"""
src/analyzers/http_timeout_checker.py

Detects `requests.get/post/put/delete/patch/head/options(...)` calls with
no `timeout=` — a call like this can hang indefinitely against a slow or
unresponsive server. The fix adds a `timeout=` keyword to a copy of the
call's AST and reconstructs it via `ast.unparse()` rather than text
templating, so it works regardless of the call's existing shape (kwargs
already present, method chaining like `requests.get(url).json()`, etc).

The fix is built from the call's ENCLOSING STATEMENT, not the call
expression in isolation — a call like `response = requests.get(url)` has
its assignment target thrown away by unparsing just the Call node, which
produced a syntactically valid but semantically broken suggestion
(`requests.get(url, timeout=10)` with `response` now referencing nothing,
a NameError anywhere `response` is used afterward). Confirmed live: an
auto-generated suggestion built this way was accepted verbatim on a real
PR and broke the file. _enclosing_statement()/_build_fix_line() below
walk up to the nearest ast.stmt ancestor (Assign, Return, Expr, ...) and
reconstruct that whole statement with only the timeout kwarg added,
so whatever wraps the call is preserved.

Only fires when the file actually imports `requests` — otherwise the call
itself is a NameError, a different problem this checker isn't about.
"""
from __future__ import annotations

import ast
import copy

from src.core.models import ConfidenceTier, Finding, Severity

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
_DEFAULT_TIMEOUT_SECONDS = 10


def _imports_requests(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "requests" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "requests":
            return True
    return False


def _has_timeout_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "timeout" for kw in call.keywords)


def _enclosing_statement(tree: ast.AST, target: ast.AST) -> ast.stmt:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    node = target
    while not isinstance(node, ast.stmt):
        node = parents[id(node)]
    return node


def _build_fix_line(stmt: ast.stmt, target_call: ast.Call, original_line: str) -> str:
    stmt_copy = copy.deepcopy(stmt)
    # Identity doesn't survive deepcopy, but (lineno, col_offset) does --
    # a reliable way to find the same Call node in the copy, since two
    # calls can't share an exact source position within one statement.
    target_copy = next(
        node
        for node in ast.walk(stmt_copy)
        if isinstance(node, ast.Call)
        and node.lineno == target_call.lineno
        and node.col_offset == target_call.col_offset
    )
    target_copy.keywords.append(
        ast.keyword(arg="timeout", value=ast.Constant(value=_DEFAULT_TIMEOUT_SECONDS))
    )
    indent = " " * (len(original_line) - len(original_line.lstrip()))
    return f"{indent}{ast.unparse(stmt_copy)}"


def detect_unguarded_http_calls(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    if not _imports_requests(tree):
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _HTTP_METHODS
            and isinstance(func.value, ast.Name)
            and func.value.id == "requests"
        ):
            continue
        if _has_timeout_kwarg(node):
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        original_line = lines[node.lineno - 1]
        stmt = _enclosing_statement(tree, node)
        fix_code = _build_fix_line(stmt, node, original_line)

        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="runtime",
                severity=Severity.WARNING,
                message=(
                    f"requests.{func.attr}(...) has no timeout — can hang "
                    f"indefinitely against a slow or unresponsive server."
                ),
                bad_code=original_line.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="http_timeout_checker",
            )
        )

    return findings
