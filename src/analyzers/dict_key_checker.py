"""
src/analyzers/dict_key_checker.py

Detects dict[literal_key] access with no existence guard, on a dict whose
exact contents can't be verified statically — a function parameter (the
caller could pass anything). Every unguarded access to the same parameter
is consolidated into one finding + fix (inserted at the top of the
function) instead of one duplicate finding per key access.

Deliberately narrow scope, to keep false positives low:
  - Only flags PARAMETERS, not arbitrary dict-valued variables — a dict
    built locally from a literal or `dict(...)`/comprehension has knowable
    contents; a parameter's don't.
  - Only literal string keys (`d["x"]`), not `d[some_variable]` — a
    dynamic key can't be checked against without more context anyway.
  - Skips a key already guarded by `if "x" in d`, `"x" not in d`, a
    `.get(...)` call visible anywhere on the same dict, or a surrounding
    try/except KeyError.
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


def _already_guarded(func: ast.AST, var_name: str, key: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            left_is_key = isinstance(node.left, ast.Constant) and node.left.value == key
            ops_ok = any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            comparators_have_var = any(
                isinstance(c, ast.Name) and c.id == var_name for c in node.comparators
            )
            if left_is_key and ops_ok and comparators_have_var:
                return True
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == var_name
            ):
                return True
        if isinstance(node, ast.Try):
            names = [n for h in node.handlers for n in exception_names(h.type)]
            if any(n in ("KeyError", "Exception") for n in names):
                return True
    return False


def detect_unguarded_dict_access(code: str, filename: str) -> list[Finding]:
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

        by_var: dict[str, list[str]] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
                continue
            var_name = node.value.id
            if var_name not in params:
                continue
            if owning_function(parent_map, node) is not func:
                continue  # belongs to a nested function shadowing the same param name

            key_node = node.slice
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            key = key_node.value
            if _already_guarded(func, var_name, key):
                continue

            by_var.setdefault(var_name, [])
            if key not in by_var[var_name]:
                by_var[var_name].append(key)

        for var_name, keys in by_var.items():
            target_line = func.lineno
            if not (0 < target_line <= len(lines)):
                continue
            def_line = lines[target_line - 1]
            body_indent = line_indent(def_line) + "    "

            keys_display = ", ".join(f"'{k}'" for k in keys)
            missing_check = " or ".join(f'"{k}" not in {var_name}' for k in keys)
            check = (
                f'{body_indent}if {missing_check}:\n'
                f'{body_indent}    raise KeyError(f"'
                f"'{var_name}' is missing required key(s): "
                f'{{[k for k in ({keys!r}) if k not in {var_name}]}}")'
            )
            fix_code = f"{def_line}\n{check}"

            findings.append(
                Finding(
                    file=filename,
                    line=target_line,
                    category="runtime",
                    severity=Severity.WARNING,
                    message=(
                        f"'{var_name}' is accessed with key"
                        f"{'s' if len(keys) > 1 else ''} {keys_display} with no "
                        f"existence check — raises KeyError if the caller doesn't "
                        f"include {'them' if len(keys) > 1 else 'it'}."
                    ),
                    bad_code=def_line.strip(),
                    fix=fix_code,
                    confidence=ConfidenceTier.MEDIUM,
                    source="dict_key_checker",
                )
            )

    return findings
