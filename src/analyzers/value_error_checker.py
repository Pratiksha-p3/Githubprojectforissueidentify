"""
src/analyzers/value_error_checker.py

Detects `int(<literal>)` / `float(<literal>)` where the literal is a
string that Python's own int()/float() would reject -- e.g. `int("abc")`
-- a guaranteed ValueError every time that line runs, not just a
possible one. Same certainty as this package's other literal-based
checkers (index_guard_checker, dict_key_checker, type_mismatch_checker,
invalid_method_checker): the argument's exact value is known from
literal syntax, so int()/float() are called directly, in a try/except,
to get CPython's own real answer rather than reimplementing its parsing
rules (leading/trailing whitespace, sign characters, "inf"/"nan" for
float, etc.).

Only resolves two argument sources, matching this package's
conservative scope: a string literal, or a variable assigned to a
string literal exactly once anywhere in the file and never reassigned.
Anything else (a parameter, a call result, an f-string, ...) isn't
reasoned about.

Only the single-argument form is checked -- `int(x, base)` changes what
counts as valid (e.g. "ff" is valid for base 16), and that second
argument isn't always a literal either, so it's left alone rather than
guessed at.

No fix is generated: the correct resolution (a different literal, a
try/except around the real call, or this line shouldn't exist at all)
depends on intent this project has no way to infer, same stance
src/analyzers/invalid_method_checker.py already takes.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity

_CONVERTERS = {"int": int, "float": float}


def _string_literal_value(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _single_assignment_string_literals(tree: ast.AST) -> dict[str, str]:
    """Maps variable name -> string value, for every name assigned to a
    string literal EXACTLY ONCE anywhere in the file, and never
    reassigned to anything else -- same conservative scope as this
    package's other literal-tracking checkers."""
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        assignments.setdefault(target.id, []).append(node.value)

    result: dict[str, str] = {}
    for name, values in assignments.items():
        if len(values) != 1:
            continue
        value = _string_literal_value(values[0])
        if value is not None:
            result[name] = value
    return result


def detect_guaranteed_value_errors(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    tracked = _single_assignment_string_literals(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        func_name = node.func.id
        converter = _CONVERTERS.get(func_name)
        if converter is None:
            continue
        if len(node.args) != 1 or node.keywords:
            continue  # int(x, base) changes what's valid -- not reasoned about

        arg = node.args[0]
        literal_value = _string_literal_value(arg)
        if literal_value is not None:
            described = f'the literal "{literal_value}"'
        elif isinstance(arg, ast.Name) and arg.id in tracked:
            literal_value = tracked[arg.id]
            described = f'\'{arg.id}\', which was assigned "{literal_value}"'
        else:
            continue

        try:
            converter(literal_value)
            continue  # genuinely valid -- not a finding
        except ValueError:
            pass

        if not (0 < node.lineno <= len(lines)):
            continue

        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=(
                    f"{func_name}({described}) — guaranteed ValueError every "
                    f"time this line runs, not just a possible one."
                ),
                bad_code=lines[node.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="value_error_checker",
            )
        )

    return findings
