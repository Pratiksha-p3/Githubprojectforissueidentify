"""
src/analyzers/index_guard_checker.py

Covers two IndexError shapes, deliberately kept separate because their
certainty is completely different:

detect_unguarded_index_access() -- `param[N]` (an integer-literal index)
on a function PARAMETER with no guard against the sequence being too
short -- raises IndexError if the CALLER passes fewer than N+1 items.
This is a "might happen depending on what's passed in" finding (WARNING,
MEDIUM confidence, a suggested guard as the fix), mirroring
src/analyzers/dict_key_checker.py's shape/scope almost exactly, just for
sequence indexing instead of dict keys.
  - Only flags PARAMETERS, not arbitrary list-valued variables -- a list
    built locally has knowable length; a parameter's doesn't.
  - Only literal integer indices (`param[0]`), not `param[i]` with a
    variable index -- a loop variable bounded by e.g. `range(len(param))`
    is already safe, and there's no way to verify that generally.
  - Skips a parameter already guarded by `if param`, `if len(param) > N`,
    `len(param) >= N+1`, or a surrounding try/except IndexError.

_literal_out_of_bounds_findings() -- `[a, b, c][N]` (or the tuple
equivalent): a LITERAL list/tuple indexed with a literal integer outside
its own known length. Unlike the parameter case, there's no "might" here
-- the literal's length is an AST-verifiable fact, so this is CRITICAL,
not WARNING, and doesn't need any guard-detection at all (there's no
caller whose input could make the literal itself longer). Still no fix:
even though the FINDING is certain, the correct resolution (a typo in
the index? a missing element? dead code that should be deleted?) isn't
derivable from the literal alone -- detection only, same stance
src/analyzers/sql_injection_checker.py already takes for a different
reason.
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


def _single_assignment_literal_lengths(tree: ast.AST) -> dict[str, int]:
    """Maps variable name -> length, for every name assigned to a
    list/tuple literal EXACTLY ONCE anywhere in the file, and never
    reassigned to anything else. Deliberately conservative: this project
    has no real dataflow analysis to know which assignment reaches which
    use, so a name assigned more than once anywhere (even in an
    unrelated function -- names aren't scope-qualified here) is dropped
    entirely rather than risk pairing a stale length with a later use."""
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        assignments.setdefault(target.id, []).append(node.value)

    return {
        name: len(values[0].elts)
        for name, values in assignments.items()
        if len(values) == 1 and isinstance(values[0], (ast.List, ast.Tuple))
    }


def _int_index_value(node: ast.expr) -> int | None:
    """The integer value of a literal index, handling BOTH shapes Python's
    AST uses for one: a positive literal (`3`) is a single ast.Constant,
    but a negative literal (`-3`) is NOT -- ast.parse() never folds unary
    minus, so it's ast.UnaryOp(USub, Constant(3)) instead. Missing the
    second shape meant every negative-index case (`x[-5]` on a 3-tuple,
    just as out of bounds as `x[5]`) was silently never even recognized
    as a literal at all, let alone bounds-checked -- caught live before
    it shipped by testing the negative-index case directly, not assumed
    to work because the positive case did."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(
        node.value, bool
    ):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _literal_out_of_bounds_findings(
    tree: ast.AST, filename: str, lines: list[str]
) -> list[Finding]:
    tracked_vars = _single_assignment_literal_lengths(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue

        if isinstance(node.value, (ast.List, ast.Tuple)):
            length = len(node.value.elts)
            kind = "list" if isinstance(node.value, ast.List) else "tuple"
            described = f"This {kind} literal has {length} item(s)"
        elif isinstance(node.value, ast.Name) and node.value.id in tracked_vars:
            length = tracked_vars[node.value.id]
            described = f"'{node.value.id}' was assigned a literal with {length} item(s)"
        else:
            continue

        index = _int_index_value(node.slice)
        if index is None:
            continue
        # Python allows -length..length-1; anything outside that range is
        # always an IndexError, regardless of what's in the literal.
        if -length <= index < length:
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
                    f"{described}, but is indexed at [{index}] — guaranteed "
                    f"IndexError every time this line runs, not just a possible one."
                ),
                bad_code=lines[node.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="index_guard_checker",
            )
        )
    return findings


def detect_unguarded_index_access(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = list(_literal_out_of_bounds_findings(tree, filename, lines))
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
