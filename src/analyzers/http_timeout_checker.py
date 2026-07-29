"""
src/analyzers/http_timeout_checker.py

Detects `requests.get/post/put/delete/patch/head/options(...)` calls with
no `timeout=` — a call like this can hang indefinitely against a slow or
unresponsive server. The fix adds a `timeout=` keyword to a copy of the
call's AST and reconstructs it via `ast.unparse()` rather than text
templating, so it works regardless of the call's existing shape (kwargs
already present, method chaining like `requests.get(url).json()`, etc).

Only fires when the file actually imports `requests` — otherwise the call
itself is a NameError, a different problem this checker isn't about.
"""
from __future__ import annotations

import ast

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

        timeout_kwarg = ast.keyword(
            arg="timeout", value=ast.Constant(value=_DEFAULT_TIMEOUT_SECONDS)
        )
        fixed_call = ast.Call(
            func=node.func,
            args=list(node.args),
            keywords=[*node.keywords, timeout_kwarg],
        )
        ast.copy_location(fixed_call, node)
        fix_expr = ast.fix_missing_locations(fixed_call)

        original_line = lines[node.lineno - 1]
        indent = " " * (len(original_line) - len(original_line.lstrip()))
        fix_code = f"{indent}{ast.unparse(fix_expr)}"

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
