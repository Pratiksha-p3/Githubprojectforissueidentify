"""
src/analyzers/unsafe_yaml_checker.py

Detects `yaml.load(x)` with no `Loader=` at all, or an explicitly unsafe
one (`yaml.Loader`, `yaml.UnsafeLoader`, `yaml.FullLoader`) -- PyYAML's
plain `Loader`/no-Loader default can construct arbitrary Python objects
from the input, which is a known remote-code-execution vector when the
YAML comes from an untrusted source (a request body, an uploaded file,
...). `yaml.SafeLoader` (equivalently `yaml.safe_load(...)`) restricts
construction to plain Python types and closes that off.

The fix rewrites the call to `yaml.safe_load(...)` (dropping any Loader=
kwarg, since safe_load already implies SafeLoader) via the same
deepcopy-and-reconstruct-the-enclosing-statement approach as
src/analyzers/http_timeout_checker.py -- unparsing just the Call node in
isolation would throw away an assignment target the same way that
checker's docstring already documents a real, live incident from.

Only fires when the file actually imports `yaml` as a plain `import
yaml` (matching hardcoded_secret_checker.py's `import os` precedent) --
`yaml.safe_load(...)`/`yaml.SafeLoader` are only valid names to write if
that's genuinely how the module is bound in this file.
"""
from __future__ import annotations

import ast
import copy

from src.core.models import ConfidenceTier, Finding, Severity

_UNSAFE_LOADER_NAMES = {"Loader", "UnsafeLoader", "FullLoader"}


def _imports_yaml(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Import)
        and any(a.name == "yaml" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    )


def _loader_kwarg(call: ast.Call) -> ast.keyword | None:
    return next((kw for kw in call.keywords if kw.arg == "Loader"), None)


def _is_unsafe_loader_value(value: ast.expr) -> bool:
    return isinstance(value, ast.Attribute) and value.attr in _UNSAFE_LOADER_NAMES


def _enclosing_statement(tree: ast.AST, target: ast.AST) -> ast.stmt:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    node = target
    while not isinstance(node, ast.stmt):
        node = parents[id(node)]
    return node


def _build_fix_line(stmt: ast.stmt, target_call: ast.Call, original_line: str) -> str:
    stmt_copy = copy.deepcopy(stmt)
    # Position alone can't disambiguate a chained call like
    # `yaml.load(x).get(...)` -- the outer Call's position is identical
    # to the inner one's, since both start at the same leftmost token.
    # See src/analyzers/http_timeout_checker.py's _build_fix_line() for
    # the same bug, caught live there first. Comparing the callee
    # expression's dump too disambiguates them.
    target_dump = ast.dump(target_call.func)
    target_copy = next(
        node
        for node in ast.walk(stmt_copy)
        if isinstance(node, ast.Call)
        and node.lineno == target_call.lineno
        and node.col_offset == target_call.col_offset
        and ast.dump(node.func) == target_dump
    )
    assert isinstance(target_copy.func, ast.Attribute)  # narrowed by caller
    target_copy.func.attr = "safe_load"
    target_copy.keywords = [kw for kw in target_copy.keywords if kw.arg != "Loader"]
    indent = " " * (len(original_line) - len(original_line.lstrip()))
    return f"{indent}{ast.unparse(stmt_copy)}"


def detect_unsafe_yaml_load(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    if not _imports_yaml(tree):
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "load"
            and isinstance(func.value, ast.Name)
            and func.value.id == "yaml"
        ):
            continue

        loader_kw = _loader_kwarg(node)
        if loader_kw is not None and not _is_unsafe_loader_value(loader_kw.value):
            continue  # an explicit SAFE (or otherwise unrecognized) Loader -- leave alone
        if not (0 < node.lineno <= len(lines)):
            continue

        original_line = lines[node.lineno - 1]
        stmt = _enclosing_statement(tree, node)
        fix_code = _build_fix_line(stmt, node, original_line)

        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="security",
                severity=Severity.CRITICAL,
                message=(
                    "yaml.load(...) with no Loader (or an unsafe one) can "
                    "construct arbitrary Python objects from the input — a "
                    "known code-execution risk if the YAML comes from an "
                    "untrusted source. Use yaml.safe_load(...) instead."
                ),
                bad_code=original_line.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="unsafe_yaml_checker",
            )
        )

    return findings
