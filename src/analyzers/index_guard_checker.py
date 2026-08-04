"""
src/analyzers/index_guard_checker.py

Detects `param[N]` (an integer-literal index) on a function parameter
with no guard against the sequence being too short -- raises IndexError
if the caller passes fewer than N+1 items. Mirrors
src/analyzers/dict_key_checker.py's shape/scope almost exactly, just for
sequence indexing instead of dict keys.

Deliberately narrow, to keep false positives low:
  - Only flags PARAMETERS, not arbitrary list-valued variables -- a list
    built locally has knowable length; a parameter's doesn't.
  - Only literal integer indices (`param[0]`), not `param[i]` with a
    variable index -- a loop variable bounded by e.g. `range(len(param))`
    is already safe, and there's no way to verify that generally.
  - Skips a parameter already guarded by `if param`, `if len(param) > N`,
    `len(param) >= N+1`, or a surrounding try/except IndexError.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import (
    build_parent_map,
    exception_names,
    line_indent,
    owning_function,
    param_names,
)
from src.core.models import ConfidenceTier, Finding, Severity


def _len_guard_covers(func: ast.AST, var_name: str, index: int) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            left = node.left
            if not (
                isinstance(left, ast.Call)
                and isinstance(left.func, ast.Name)
                and left.func.id == "len"
                and len(left.args) == 1
                and isinstance(left.args[0], ast.Name)
                and left.args[0].id == var_name
            ):
                continue
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                if not isinstance(comparator, ast.Constant) or not isinstance(
                    comparator.value, int
                ):
                    continue
                threshold = comparator.value
                if isinstance(op, ast.Gt) and threshold >= index:
                    return True
                if isinstance(op, ast.GtE) and threshold >= index + 1:
                    return True
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == var_name
        ):
            return True  # `if param:` -- guards against empty, covers index 0
        if isinstance(node, ast.Try):
            names = [n for h in node.handlers for n in exception_names(h.type)]
            if any(n in ("IndexError", "Exception") for n in names):
                return True
    return False


def detect_unguarded_index_access(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = []
    seen_funcs: set[int] = set()

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(func) in seen_funcs:
            continue
        seen_funcs.add(id(func))

        params = param_names(func)
        if not params:
            continue

        by_var: dict[str, int] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
                continue
            var_name = node.value.id
            if var_name not in params:
                continue
            if owning_function(parent_map, node) is not func:
                continue

            index_node = node.slice
            if not (isinstance(index_node, ast.Constant) and isinstance(index_node.value, int)):
                continue
            index = index_node.value
            if index < 0:
                continue  # negative indexing has different (harder to guard) semantics
            if _len_guard_covers(func, var_name, index):
                continue

            by_var[var_name] = max(by_var.get(var_name, -1), index)

        for var_name, max_index in by_var.items():
            target_line = func.lineno
            if not (0 < target_line <= len(lines)):
                continue
            def_line = lines[target_line - 1]
            body_indent = line_indent(def_line) + "    "

            check = (
                f"{body_indent}if len({var_name}) <= {max_index}:\n"
                f"{body_indent}    raise IndexError(f\"'{var_name}' has fewer than "
                f'{max_index + 1} item(s)")'
            )
            fix_code = f"{def_line}\n{check}"

            findings.append(
                Finding(
                    file=filename,
                    line=target_line,
                    category="runtime",
                    severity=Severity.WARNING,
                    message=(
                        f"'{var_name}' is indexed at [{max_index}] with no length "
                        f"check — raises IndexError if the caller passes fewer than "
                        f"{max_index + 1} item(s)."
                    ),
                    bad_code=def_line.strip(),
                    fix=fix_code,
                    confidence=ConfidenceTier.MEDIUM,
                    source="index_guard_checker",
                )
            )

    return findings
