"""
src/analyzers/insecure_deserialization_checker.py

Detects `pickle.load(...)` / `pickle.loads(...)` -- unpickling data
executes arbitrary code embedded in the pickle stream by design (it's
how pickle reconstructs objects), so deserializing anything from an
untrusted source (a request body, an uploaded file, a message queue) is
a direct remote-code-execution vector, not a hypothetical one.

Only fires when the file imports `pickle` as a plain `import pickle`.

No fix is generated: the correct remediation depends entirely on what
the data actually needs to represent -- switching to `json`, only
un-pickling from a trusted/signed source, or restricting the unpickler
with a custom `Unpickler.find_class()` allowlist are all valid answers
in different situations, none of which is derivable from the call site
alone. Same "detection, not auto-fix" stance
src/analyzers/sql_injection_checker.py already takes.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity

_UNSAFE_FUNCS = {"load", "loads"}


def _imports_pickle(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Import)
        and any(a.name == "pickle" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    )


def detect_insecure_deserialization(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    if not _imports_pickle(tree):
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _UNSAFE_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id == "pickle"
        ):
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        original_line = lines[node.lineno - 1]
        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="security",
                severity=Severity.CRITICAL,
                message=(
                    f"pickle.{func.attr}(...) can execute arbitrary code embedded "
                    f"in the pickle stream — deserializing data from any source "
                    f"you don't fully trust is a remote-code-execution risk, not "
                    f"just a data-integrity one. Use json (or another data-only "
                    f"format) instead, or verify the source is trusted/signed."
                ),
                bad_code=original_line.strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="insecure_deserialization_checker",
            )
        )

    return findings
