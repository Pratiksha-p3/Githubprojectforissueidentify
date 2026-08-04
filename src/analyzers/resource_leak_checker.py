"""
src/analyzers/resource_leak_checker.py

Detects `x = open(...)` assigned outside a `with` block, where `x` is
never `.close()`'d, never returned, and never passed to another call
anywhere in the enclosing function -- the file handle is opened and
then leaked, staying open (holding the OS file descriptor, and on
Windows blocking other processes from deleting/renaming the file) for
the remaining lifetime of the process.

Deliberately conservative to keep false positives low: `x` being
returned or passed as an argument means SOME other code might still be
responsible for closing it (ownership transferred out of this
function), which this checker can't verify one way or the other -- so
it stays silent rather than risk a wrong flag. This mirrors
src/analyzers/file_exists_checker.py's scope (also `open(...)`-based)
but is a genuinely different bug: that one is about a missing existence
guard before the call; this one is about what happens to the handle
after a successful call.

No fix is generated: correctly restructuring every subsequent use of
`x` into the body of a `with open(...) as x:` block requires knowing
the full extent of that usage, which src/analyzers/file_exists_checker.py's
own history already shows is easy to get wrong when a fix is applied
without the body being included -- safer to flag it and let a human
choose between `with` and an explicit `.close()`/try-finally.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import build_parent_map, owning_function
from src.core.models import ConfidenceTier, Finding, Severity


def _is_open_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
    )


def _is_closed_returned_or_passed(scope: ast.AST, name: str, assign_node: ast.AST) -> bool:
    for node in ast.walk(scope):
        if node is assign_node:
            continue
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "close"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == name
            ):
                return True
            if any(isinstance(a, ast.Name) and a.id == name for a in node.args):
                return True  # passed elsewhere -- can't verify who owns closing it
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
        ):
            return True
        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Name) and ctx.id == name:
                    return True  # later reopened/rebound under a `with` -- not this one's problem
    return False


def detect_unclosed_file_handles(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _is_open_call(node.value):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(parent_map.get(id(node)), ast.With):
            continue  # `x = open(...)` as a with-item's own internal form -- not this shape

        var_name = node.targets[0].id
        scope = owning_function(parent_map, node) or tree
        if _is_closed_returned_or_passed(scope, var_name, node):
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        original_line = lines[node.lineno - 1]
        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="runtime",
                severity=Severity.WARNING,
                message=(
                    f"'{var_name}' is opened via open(...) but never closed, "
                    f"returned, or passed elsewhere — the file handle leaks "
                    f"for the rest of the process's lifetime. Use "
                    f"`with open(...) as {var_name}:` instead."
                ),
                bad_code=original_line.strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="resource_leak_checker",
            )
        )

    return findings
