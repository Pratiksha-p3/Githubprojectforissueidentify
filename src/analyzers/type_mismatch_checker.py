"""
src/analyzers/type_mismatch_checker.py

Detects `+` between two operands whose types are provably incompatible
for concatenation/addition -- e.g. `"count: " + 5` or `[1, 2] + (3, 4)`
-- a guaranteed TypeError every time that line runs. Same certainty as
src/analyzers/index_guard_checker.py's and
src/analyzers/dict_key_checker.py's literal-based findings, for the same
reason: no type inference engine is needed when both operand types are
already known literal constants (or a variable tracked back to one).

Deliberately scoped to `+` only, not `-`/`*`/`/`/... -- those have
different, more permissive compatibility rules this checker doesn't
reason about at all (`"ab" * 3` is valid, repeating the string; treating
`*` the same as `+` would produce false positives). This is not a type
checker; it's one narrow, provable shape.

Only two operand sources are resolved, matching the same conservative
scope as index_guard_checker's and dict_key_checker's equivalents:
  - A literal (`ast.Constant`, or a `list`/`tuple`/`dict`/`set` literal).
  - A variable assigned to a literal EXACTLY ONCE anywhere in the file,
    and never reassigned to anything else -- a name reassigned anywhere
    (even in an unrelated function) is dropped entirely rather than risk
    pairing a stale type with a later, differently-sourced use.
Anything else (a function call's result, an attribute access, an
f-string, ...) isn't reasoned about, since its actual type genuinely
isn't knowable without real type inference.

`dict` and `set` literals are ALWAYS incompatible with `+`, including
with each other -- neither type defines `__add__` at all (dicts don't
support concatenation; sets use `|` for union), so `{} + {}` is just as
guaranteed a TypeError as `"x" + 5`.

No fix is generated: the correct resolution (convert one operand,
recognize the whole expression is wrong, ...) depends entirely on what
was actually meant, same "detection, not auto-fix" stance
src/analyzers/sql_injection_checker.py already takes for a bug class
whose safe rewrite depends on context this project doesn't have.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity

_NUMERIC = (int, float)  # bool is a subclass of int, arithmetically compatible
_NEVER_ADDABLE = {"dict", "set"}  # neither type defines __add__ at all


def _category(node: ast.expr) -> str | None:
    """A coarse type category for a literal expression, or None if this
    checker doesn't reason about it at all (not a type inference
    engine -- only syntax that IS a literal in the AST is covered)."""
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or isinstance(value, _NUMERIC):
            return "numeric"
        if isinstance(value, str):
            return "string"
        if isinstance(value, bytes):
            return "bytes"
        return None  # None, Ellipsis, complex, ... -- not reasoned about here
    if isinstance(node, ast.List):
        return "list"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, ast.Dict):
        return "dict"
    if isinstance(node, ast.Set):
        return "set"
    return None


def _incompatible(left: str, right: str) -> bool:
    if left in _NEVER_ADDABLE or right in _NEVER_ADDABLE:
        return True
    if left == "numeric" and right == "numeric":
        return False
    return left != right


def _single_assignment_literal_categories(tree: ast.AST) -> dict[str, str]:
    """Maps variable name -> category, for every name assigned to a
    literal EXACTLY ONCE anywhere in the file, and never reassigned to
    anything else."""
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
        category = _category(values[0])
        if category is not None:
            result[name] = category
    return result


def _resolve(node: ast.expr, tracked: dict[str, str]) -> tuple[str, str] | None:
    """Returns (category, display text) for an operand this checker can
    reason about, or None otherwise. Display text is just the category
    name for a bare literal (e.g. "string"), or the variable name for a
    tracked one (e.g. "'name'") -- kept separate from category so the
    message doesn't repeat "string (string)" for the common bare-literal
    case."""
    category = _category(node)
    if category is not None:
        return category, category
    if isinstance(node, ast.Name) and node.id in tracked:
        return tracked[node.id], f"'{node.id}'"
    return None


def _describe(desc: str, category: str) -> str:
    return desc if desc == category else f"{desc} ({category})"


def detect_type_mismatched_addition(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    tracked = _single_assignment_literal_categories(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
            continue

        left = _resolve(node.left, tracked)
        right = _resolve(node.right, tracked)
        if left is None or right is None:
            continue
        left_cat, left_desc = left
        right_cat, right_desc = right
        if not _incompatible(left_cat, right_cat):
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
                    f"{_describe(left_desc, left_cat)} + "
                    f"{_describe(right_desc, right_cat)} — these types can't "
                    f"be added/concatenated — guaranteed TypeError every time "
                    f"this line runs, not just a possible one."
                ),
                bad_code=lines[node.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="type_mismatch_checker",
            )
        )

    return findings
