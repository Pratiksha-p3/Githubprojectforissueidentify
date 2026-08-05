"""
src/analyzers/invalid_method_checker.py

Detects `<expr>.method(...)` where `<expr>` is a literal (or a variable
assigned to one exactly once, never reassigned) of a known Python
builtin type, and `method` isn't actually an attribute of that type --
e.g. `"hello".append("x")` (append is a list method, not a string one)
-- a guaranteed AttributeError every time that line runs. Same
certainty as index_guard_checker's, dict_key_checker's, and
type_mismatch_checker's literal-based findings, for the same reason: no
type inference engine is needed when the receiver's type is already
known from literal syntax.

This is safe specifically BECAUSE it only ever resolves to a real
Python builtin type (str, list, tuple, dict, set, bytes, int, float,
bool) -- those are immutable at the C level and can't be monkey-patched
with new methods the way a user-defined class could be, so `hasattr(the
_type, method_name)` is a reliable, permanent fact, not a snapshot that
could go stale. A literal is also always exactly its builtin type, never
a subclass -- `"x"` can't secretly be some str subclass with an extra
method, the way an arbitrary variable's runtime value could be.

Only two operand sources are resolved, matching the same conservative
scope as the other literal-based checkers in this package: a literal
expression, or a variable assigned to one exactly once anywhere in the
file and never reassigned to anything else. Anything else (a function
parameter, a call result, an attribute access, ...) isn't reasoned
about, since its actual type genuinely isn't knowable without real type
inference.

No fix is generated: the correct resolution (a different method name, a
different variable, converting the value first) depends entirely on
what was actually meant, same "detection, not auto-fix" stance
src/analyzers/sql_injection_checker.py already takes for a bug class
whose safe rewrite depends on context this project doesn't have.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity


def _literal_type(node: ast.expr) -> type | None:
    """Resolves a literal expression to its exact, real Python type for
    hasattr() introspection -- str/int/float/bool/bytes distinguished
    exactly (unlike src/analyzers/type_mismatch_checker.py's coarser
    "numeric" merge, which only cares about + compatibility, not which
    specific methods exist)."""
    if isinstance(node, ast.Constant):
        return type(node.value)
    if isinstance(node, ast.List):
        return list
    if isinstance(node, ast.Tuple):
        return tuple
    if isinstance(node, ast.Dict):
        return dict
    if isinstance(node, ast.Set):
        return set
    return None


def _single_assignment_literal_types(tree: ast.AST) -> dict[str, type]:
    """Maps variable name -> type, for every name assigned to a literal
    EXACTLY ONCE anywhere in the file, and never reassigned to anything
    else -- same conservative scope as this package's other literal-
    tracking checkers."""
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        assignments.setdefault(target.id, []).append(node.value)

    result: dict[str, type] = {}
    for name, values in assignments.items():
        if len(values) != 1:
            continue
        value_type = _literal_type(values[0])
        if value_type is not None:
            result[name] = value_type
    return result


def detect_invalid_method_calls(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    tracked = _single_assignment_literal_types(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        method_name = node.func.attr

        value_type = _literal_type(receiver)
        if value_type is not None:
            described = value_type.__name__
        elif isinstance(receiver, ast.Name) and receiver.id in tracked:
            value_type = tracked[receiver.id]
            described = f"'{receiver.id}' ({value_type.__name__})"
        else:
            continue

        if hasattr(value_type, method_name):
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=(
                    f"{described} has no '{method_name}' method — guaranteed "
                    f"AttributeError every time this line runs, not just a "
                    f"possible one."
                ),
                bad_code=lines[node.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="invalid_method_checker",
            )
        )

    return findings
