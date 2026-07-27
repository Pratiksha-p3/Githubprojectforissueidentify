# analyzers/file_exists_checker.py
"""
Detects an `open(path, ...)` call in a read mode with no existence
check, and generates a fix guarding the ACTUAL path expression —
replacing a bare `r"open\\s*\\("` regex that flagged every call to
open() regardless of mode (including write/append modes, where "the
file may not exist" isn't even a problem — those modes create it) and
whose fix template hardcoded a `path` variable and `f`/`data` names
that don't exist in the code being "fixed."
"""
from __future__ import annotations

import ast

_READ_MODES = {"r", "rt", "rb", "r+", "rb+", "r+b", "br", "br+"}


def _mode_of(call: ast.Call) -> str:
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
        return call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return "r"  # open()'s own default


def _already_guarded(parent_map: dict, call_node: ast.AST) -> bool:
    """Already inside a try/except that catches FileNotFoundError/OSError/IOError."""
    n = parent_map.get(id(call_node))
    while n is not None:
        if isinstance(n, ast.Try):
            for h in n.handlers:
                names = []
                if isinstance(h.type, ast.Name):
                    names = [h.type.id]
                elif isinstance(h.type, ast.Tuple):
                    names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
                if any(nm in ("FileNotFoundError", "OSError", "IOError") for nm in names):
                    return True
        n = parent_map.get(id(n))
    return False


def detect_unguarded_file_open(code: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

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
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"):
            continue
        if not node.args:
            continue
        if _mode_of(node) not in _READ_MODES:
            continue  # write/append/create modes make the file — nothing to guard
        if _already_guarded(parent_map, node):
            continue

        stmt = enclosing_stmt(node)
        if stmt is None:
            continue
        line = stmt.lineno
        if line in seen_lines or not (0 < line <= len(lines)):
            continue

        try:
            path_expr = ast.unparse(node.args[0])
        except Exception:
            continue

        original = lines[line - 1]
        indent = " " * (len(original) - len(original.lstrip()))
        check = (
            f'{indent}if not os.path.exists({path_expr}):\n'
            f'{indent}    raise FileNotFoundError({path_expr})'
        )
        findings.append({
            "category": "runtime",
            "severity": "warning",
            "file": filename,
            "line": line,
            "message": (
                f"open({path_expr}, ...) has no existence check — raises "
                f"FileNotFoundError if the path doesn't exist."
            ),
            "bad_code": original.strip(),
            "fix_type": "file_exists_guard",
            "fix": f"{check}\n{original}",
            "reason": f"Inserted an os.path.exists() check for {path_expr} before opening it.",
        })
        seen_lines.add(line)

    return findings
