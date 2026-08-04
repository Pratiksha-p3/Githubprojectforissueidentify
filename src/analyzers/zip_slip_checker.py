"""
src/analyzers/zip_slip_checker.py

Detects `<zipfile-like>.extractall(...)` / `.extract(...)` with no
member-path validation anywhere in the enclosing function -- a zip
archive's entry names are attacker-controlled data, and Python's
extraction does NOT strip `..` segments from them. A crafted archive
containing an entry named `"../../../etc/cron.d/evil"` writes outside
the intended extraction directory the moment it's extracted ("Zip
Slip") -- this is a well-known, still commonly-missed vulnerability
class, not a hypothetical one.

Fires on `.extractall`/`.extract` called on any object -- narrowing to
"only real zipfile.ZipFile instances" would need type inference this
project doesn't do; matches src/analyzers/sql_injection_checker.py's
same "any `.execute()`, not just a verified DB cursor" precedent.

Skipped if the function already contains a guard: iterating
`.namelist()`/`.infolist()` and checking members before extracting (the
standard safe pattern), or a `".." in` check anywhere -- same "guard
exists anywhere in the function" scope every checker in this package
uses rather than precise dataflow tracking.

No fix is generated: the safe rewrite means iterating entries and
validating each one's resolved path stays inside the target directory,
which isn't a one-line substitution -- same "detection, not auto-fix"
stance src/analyzers/sql_injection_checker.py already takes for a bug
whose safe form is structurally different code, not a tweaked call.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import build_parent_map, owning_function
from src.core.models import ConfidenceTier, Finding, Severity

_EXTRACT_METHODS = {"extractall", "extract"}


def _already_guarded(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr in ("namelist", "infolist"):
            return True
        if isinstance(node, ast.Compare):
            if isinstance(node.left, ast.Constant) and node.left.value == "..":
                if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                    return True
    return False


def detect_zip_slip(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_attr = node.func
        if not (isinstance(func_attr, ast.Attribute) and func_attr.attr in _EXTRACT_METHODS):
            continue

        func = owning_function(parent_map, node) or tree
        if _already_guarded(func):
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
                    f".{func_attr.attr}(...) extracts every member's path "
                    f"verbatim, with no check for '..' segments — a crafted "
                    f"archive entry can write outside the target directory "
                    f"('Zip Slip'). Validate each member's resolved path stays "
                    f"inside the target directory before extracting it."
                ),
                bad_code=original_line.strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="zip_slip_checker",
            )
        )

    return findings
