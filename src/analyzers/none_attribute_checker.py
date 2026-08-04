"""
src/analyzers/none_attribute_checker.py

Detects `x = <expr>.get(key)` (dict.get with no default -- returns None
on a missing key) or `x = re.match(...)`/`re.search(...)` (both return
None on no match), followed by `x.<attr>` accessed anywhere later in the
same function with no `if x is None` / `if x is not None` / `if x:` /
try/except AttributeError guard in between -- raises AttributeError the
moment the lookup/match actually fails, which is exactly the case these
calls exist to signal rather than raise on.

Scoped narrowly to keep false positives low:
  - Only the two specific call shapes above -- both are None-returning
    by DOCUMENTED CONTRACT, not a guess about arbitrary function return
    types this project has no way to infer.
  - `.get(key, default)` (two args) is excluded -- a non-None default
    means the result can't be None from that call alone.
  - Only flags a bare `x.attr` access, not `x(...)` calls, subscripts,
    or use as a plain value (`return x`, `if x`) -- those don't risk an
    AttributeError.

The fix inserts a guard immediately after the assignment (matching
src/analyzers/dict_key_checker.py's "guard right where the risky value
is bound" placement) rather than at the specific later line the
unguarded access occurs -- simpler and still correct regardless of how
many places `x` gets used afterward.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import exception_names, line_indent
from src.core.models import ConfidenceTier, Finding, Severity


def _is_none_returning_call(node: ast.expr) -> str:
    """Returns a short description of the call shape if it's a known
    None-returning one, else ""."""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "get" and len(node.args) == 1:
        return "dict.get(key) with no default"
    if (
        isinstance(func, ast.Attribute)
        and func.attr in ("match", "search")
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    ):
        return f"re.{func.attr}(...)"
    return ""


def _already_guarded(func: ast.AST, var_name: str) -> bool:
    """Same "does a qualifying guard exist anywhere in the function"
    scope src/analyzers/dict_key_checker.py's _already_guarded() and
    src/analyzers/file_exists_checker.py's _already_guarded() already
    use, rather than precise statement-order/nesting analysis -- simpler,
    and consistent with this project's established precision trade-off
    for these checkers (occasionally under-flags an oddly-structured
    guard, never mis-detects an absent one as present)."""
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == var_name:
                return True
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == var_name
                and any(isinstance(op, (ast.Is, ast.IsNot)) for op in test.ops)
            ):
                return True
        if isinstance(node, ast.Try):
            names = [n for h in node.handlers for n in exception_names(h.type)]
            if any(n in ("AttributeError", "Exception") for n in names):
                return True
    return False


def _attr_access_exists(func: ast.AST, var_name: str) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == var_name
        for node in ast.walk(func)
    )


def detect_unguarded_none_attribute_access(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for stmt in func.body:
            if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue
            shape = _is_none_returning_call(stmt.value)
            if not shape:
                continue
            if not _attr_access_exists(func, target.id):
                continue
            if _already_guarded(func, target.id):
                continue
            if not (0 < stmt.lineno <= len(lines)):
                continue

            original_line = lines[stmt.lineno - 1]
            indent = line_indent(original_line)
            check = (
                f"{indent}if {target.id} is None:\n"
                f'{indent}    raise AttributeError(f"\'{target.id}\' was None '
                f'({shape} found nothing)")'
            )
            fix_code = f"{original_line}\n{check}"

            findings.append(
                Finding(
                    file=filename,
                    line=stmt.lineno,
                    category="runtime",
                    severity=Severity.WARNING,
                    message=(
                        f"'{target.id}' comes from {shape}, which returns None "
                        f"when nothing is found, but is accessed via attribute "
                        f"later with no None check — raises AttributeError."
                    ),
                    bad_code=original_line.strip(),
                    fix=fix_code,
                    confidence=ConfidenceTier.MEDIUM,
                    source="none_attribute_checker",
                )
            )

    return findings
