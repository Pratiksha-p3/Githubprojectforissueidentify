"""
src/analyzers/path_traversal_checker.py

Detects `open(os.path.join(base, param), ...)` where `param` is a
function parameter -- a caller who controls `param` can pass something
like `"../../etc/passwd"` and read (or, combined with a write mode,
overwrite) any file the process can reach, escaping `base` entirely.
`os.path.join()` does NOT strip `..` segments or otherwise sanitize its
arguments; that's a common, understandable assumption to get wrong.

Two call shapes are covered, matching src/analyzers/sql_injection_checker.py's
same reasoning for why both matter: `open(os.path.join(base, param))`
directly, and the more common real-world shape where the join happens on
its own line first -- `path = os.path.join(base, param)` followed by
`open(path)` elsewhere in the function.

Scoped narrowly to keep false positives low -- only the `os.path.join`
shape is matched (not arbitrary string concatenation/f-strings building
a path, which would need much broader matching to catch reliably), and
skipped if the function already contains ANY of the common guards
against this: a `".." in param` check, a call to
`os.path.abspath`/`os.path.realpath`/`os.path.normpath`, or a call to
`secure_filename` (werkzeug's standard filename-sanitizing helper) --
same "does a qualifying guard exist anywhere in the function" scope
src/analyzers/dict_key_checker.py and friends already use, not precise
dataflow tracking of whether the guard's result is what actually reaches
open().

No fix is generated: the correct remediation (reject the request,
resolve-and-check-containment, or sanitize the filename) is a judgment
call about the application's actual requirements, not something
derivable from the call site alone -- same stance
src/analyzers/sql_injection_checker.py already takes.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import build_parent_map, owning_function, param_names
from src.core.models import ConfidenceTier, Finding, Severity

_GUARD_FUNC_NAMES = {"abspath", "realpath", "normpath", "secure_filename"}


def _is_os_path_join(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "path"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
    )


def _join_uses_param(node: ast.Call, params: set[str]) -> str:
    """Only looks at args[1:] -- os.path.join(base, *parts)'s FIRST
    argument is conventionally the trusted root, and it's the segments
    appended AFTER it that can contain a `..` and walk back out of that
    root. Flagging the base argument itself would point at the wrong
    (usually safe) parameter whenever both happen to be parameters."""
    for arg in node.args[1:]:
        if isinstance(arg, ast.Name) and arg.id in params:
            return arg.id
    return ""


def _join_call_assigned_to(scope: ast.AST, name: str) -> ast.Call | None:
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        if isinstance(node.value, ast.Call) and _is_os_path_join(node.value):
            return node.value
    return None


def _already_guarded(func: ast.AST, var_name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            if (
                isinstance(node.left, ast.Constant)
                and node.left.value == ".."
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                and any(
                    isinstance(c, ast.Name) and c.id == var_name for c in node.comparators
                )
            ):
                return True
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else ""
            )
            if name in _GUARD_FUNC_NAMES:
                return True
    return False


def detect_path_traversal(code: str, filename: str) -> list[Finding]:
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

        func = owning_function(parent_map, node)
        if func is None:
            continue

        join_call: ast.Call | None = None
        if isinstance(path_arg, ast.Call) and _is_os_path_join(path_arg):
            join_call = path_arg
        elif isinstance(path_arg, ast.Name):
            join_call = _join_call_assigned_to(func, path_arg.id)
        if join_call is None:
            continue

        params = param_names(func)
        param_used = _join_uses_param(join_call, params)
        if not param_used:
            continue
        if _already_guarded(func, param_used):
            continue
        if not (0 < node.lineno <= len(lines)) or node.lineno in seen_lines:
            continue
        seen_lines.add(node.lineno)

        original_line = lines[node.lineno - 1]
        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="security",
                severity=Severity.CRITICAL,
                message=(
                    f"open(os.path.join(..., {param_used}), ...) builds a file "
                    f"path from '{param_used}', a caller-controlled parameter, "
                    f"with no check against '..' path segments — a malicious "
                    f"value can escape the intended directory (path traversal), "
                    f"reading or writing files anywhere the process can reach."
                ),
                bad_code=original_line.strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="path_traversal_checker",
            )
        )

    return findings
