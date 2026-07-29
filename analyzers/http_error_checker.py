# analyzers/http_error_checker.py
"""
Detects requests.get/post/put/delete/patch/head/options(...) calls with
no timeout and/or no surrounding error handling — either one alone is a
common source of real incidents: an unhandled connection error crashes
the caller, and a call with no timeout can hang indefinitely against a
slow or unresponsive server.

The fix is built by modifying a copy of the call's AST (adding a
`timeout=` keyword if missing) and reconstructing the source via
ast.unparse(), rather than a text template — so it works correctly
regardless of how the original call is shaped (existing kwargs, method
chaining like requests.get(url).json(), multi-line calls, ...).
"""
from __future__ import annotations

import ast

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "request"}
_HTTP_EXCEPTION_NAMES = {"RequestException", "ConnectionError", "Timeout", "HTTPError", "Exception"}


def _is_requests_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in _HTTP_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
    )


def _has_timeout(node: ast.Call) -> bool:
    return any(kw.arg == "timeout" for kw in node.keywords)


def _exception_names(h_type) -> list[str]:
    """Handler exception name(s) — handles bare (`ValueError`), dotted
    (`requests.exceptions.RequestException` — how this specific
    exception is always actually written, never as a bare name), and
    tuple (`except (A, B):`) forms."""
    if isinstance(h_type, ast.Name):
        return [h_type.id]
    if isinstance(h_type, ast.Attribute):
        return [h_type.attr]
    if isinstance(h_type, ast.Tuple):
        names = []
        for e in h_type.elts:
            if isinstance(e, ast.Name):
                names.append(e.id)
            elif isinstance(e, ast.Attribute):
                names.append(e.attr)
        return names
    return []


def _already_guarded(parent_map: dict, node: ast.AST) -> bool:
    n = parent_map.get(id(node))
    while n is not None:
        if isinstance(n, ast.Try):
            names = [nm for h in n.handlers for nm in _exception_names(h.type)]
            if any(nm in _HTTP_EXCEPTION_NAMES for nm in names):
                return True
        n = parent_map.get(id(n))
    return False


def detect_unguarded_http_calls(code: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    # requests.get(...) is only this specific risk if the file actually
    # imports `requests` — otherwise the call itself is a NameError, a
    # different (and already-covered) problem.
    imports_requests = any(
        isinstance(n, ast.Import) and any(a.name == "requests" for a in n.names)
        for n in ast.walk(tree)
    )
    if not imports_requests:
        return []

    parent_map: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    def enclosing_stmt(node):
        n = node
        while n is not None and not isinstance(n, ast.stmt):
            n = parent_map.get(id(n))
        return n

    findings = []
    seen_lines = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_requests_call(node)):
            continue

        has_timeout = _has_timeout(node)
        is_guarded = _already_guarded(parent_map, node)
        if has_timeout and is_guarded:
            continue

        stmt = enclosing_stmt(node)
        if stmt is None:
            continue
        line = stmt.lineno
        if line in seen_lines or not (0 < line <= len(lines)):
            continue

        original = lines[line - 1]
        indent = " " * (len(original) - len(original.lstrip()))

        if not has_timeout:
            node.keywords.append(ast.keyword(arg="timeout", value=ast.Constant(value=10)))
        try:
            new_stmt_text = ast.unparse(stmt)
        except Exception:
            continue

        method = node.func.attr
        missing = []
        if not has_timeout:
            missing.append("no timeout (can hang indefinitely)")

        if is_guarded:
            # Error handling already exists — the call only needed the
            # timeout added, not a second try/except wrapped around it.
            fix_code = f"{indent}{new_stmt_text}"
        else:
            missing.append("no error handling for connection/HTTP failures")
            fix_code = (
                f"{indent}try:\n"
                f"{indent}    {new_stmt_text}\n"
                f"{indent}except requests.exceptions.RequestException as e:\n"
                f'{indent}    raise RuntimeError(f"requests.{method}() call failed: {{e}}") from e'
            )

        findings.append({
            "category": "runtime",
            "severity": "warning",
            "file": filename,
            "line": line,
            "message": f"requests.{method}() has {' and '.join(missing)}.",
            "bad_code": original.strip(),
            "fix_type": "http_error_guard",
            "fix": fix_code,
            "reason": "Network calls can fail or hang — wrap in try/except and always set a timeout.",
        })
        seen_lines.add(line)

    return findings
