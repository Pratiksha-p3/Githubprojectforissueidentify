"""
src/analyzers/insecure_http_checker.py

Detects a hardcoded `"http://..."` string literal — traffic to it is
unencrypted, so anything sent (credentials, tokens, request/response
bodies) is readable and tamperable in transit. The fix rewrites the
literal to `"https://..."` by reconstructing the literal's enclosing
statement (same deepcopy/find-by-position/ast.unparse approach as
src/analyzers/http_timeout_checker.py), not a text-level string
replace, so it works regardless of where the literal sits in the
statement (an assignment, a call argument, part of an f-string, ...).

Scoped narrowly to keep false positives low:
  - `http://localhost`, `http://127.0.0.1`, and `http://0.0.0.0` are
    skipped — dev/test loopback traffic never leaves the machine, and
    those hosts often genuinely have no TLS listener to upgrade to.
  - Only a literal that is ENTIRELY `http://...` (an ast.Constant str)
    is matched — an f-string built at runtime from a variable base URL
    isn't something this checker can safely rewrite.
"""
from __future__ import annotations

import ast
import copy

from src.core.models import ConfidenceTier, Finding, Severity

_LOOPBACK_HOSTS = ("http://localhost", "http://127.0.0.1", "http://0.0.0.0")


def _is_insecure_url(value: str) -> bool:
    if not value.lower().startswith("http://"):
        return False
    return not value.lower().startswith(_LOOPBACK_HOSTS)


def _enclosing_statement(tree: ast.AST, target: ast.AST) -> ast.stmt:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    node = target
    while not isinstance(node, ast.stmt):
        node = parents[id(node)]
    return node


def _build_fix_line(stmt: ast.stmt, target_const: ast.Constant, original_line: str) -> str:
    stmt_copy = copy.deepcopy(stmt)
    target_copy = next(
        node
        for node in ast.walk(stmt_copy)
        if isinstance(node, ast.Constant)
        and node.lineno == target_const.lineno
        and node.col_offset == target_const.col_offset
    )
    assert isinstance(target_copy.value, str)  # narrowed by caller's _is_insecure_url check
    target_copy.value = "https://" + target_copy.value[len("http://") :]
    indent = " " * (len(original_line) - len(original_line.lstrip()))
    return f"{indent}{ast.unparse(stmt_copy)}"


def detect_insecure_http_urls(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not _is_insecure_url(node.value):
            continue
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
                severity=Severity.WARNING,
                message=(
                    f"'{node.value}' uses unencrypted HTTP — traffic to it "
                    f"(and anything sent over it) can be read or tampered "
                    f"with in transit. Use HTTPS instead."
                ),
                bad_code=original_line.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="insecure_http_checker",
            )
        )

    return findings
