"""
src/analyzers/weak_crypto_checker.py

Detects `hashlib.md5(...)` / `hashlib.sha1(...)` — both are cryptographically
broken (practical collision attacks exist for both), so using either for
anything security-sensitive (password hashing, signatures, integrity
checks against a hostile party) is a real weakness. The fix swaps the
call for `hashlib.sha256(...)`, reconstructing the enclosing statement
via the same deepcopy/find-by-position/ast.unparse approach as
src/analyzers/http_timeout_checker.py so an assignment target or other
surrounding structure survives.

This can't tell whether a given call is actually being used for security
purposes (a checksum for cache-busting or de-duplication is a completely
legitimate use of md5) -- flagged anyway, same trade-off
src/analyzers/insecure_http_checker.py already makes for hardcoded
http:// URLs: false positives here are a wasted suggestion, not a wrong
one, since sha256 is a safe drop-in replacement either way (same call
shape, just a different, larger digest).

Only fires when the file imports `hashlib` as a plain `import hashlib`.
"""
from __future__ import annotations

import ast
import copy

from src.core.models import ConfidenceTier, Finding, Severity

_WEAK_ALGORITHMS = {"md5", "sha1"}


def _imports_hashlib(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Import)
        and any(a.name == "hashlib" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    )


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
    # `hashlib.md5(x).hexdigest()` -- the outer Call's position is
    # identical to the inner one's, since both start at the same
    # leftmost token. See src/analyzers/http_timeout_checker.py's
    # _build_fix_line() for the same bug, caught live there first.
    # Comparing the callee expression's dump too disambiguates them.
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
    target_copy.func.attr = "sha256"
    indent = " " * (len(original_line) - len(original_line.lstrip()))
    return f"{indent}{ast.unparse(stmt_copy)}"


def detect_weak_crypto(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    if not _imports_hashlib(tree):
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _WEAK_ALGORITHMS
            and isinstance(func.value, ast.Name)
            and func.value.id == "hashlib"
        ):
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
                    f"hashlib.{func.attr}(...) is a cryptographically broken "
                    f"hash algorithm — unsafe for signatures or any integrity "
                    f"check against a hostile party. hashlib.sha256(...) is a "
                    f"safe drop-in replacement for general hashing; if this is "
                    f"hashing a PASSWORD specifically, use a dedicated password "
                    f"hash (bcrypt/scrypt/argon2) instead of any general-purpose "
                    f"digest, sha256 included."
                ),
                bad_code=original_line.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="weak_crypto_checker",
            )
        )

    return findings
