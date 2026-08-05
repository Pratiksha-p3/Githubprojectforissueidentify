"""
src/analyzers/unused_import_checker.py

Detects an import that's never referenced anywhere in the file --
harmless at runtime, but dead weight that misleads a reader about the
module's actual dependencies.

Every other checker in this package matches an AST shape by hand; this
one doesn't reimplement "is this name referenced anywhere" (the same
kind of full-scope analysis src/analyzers/undefined_name_checker.py
already wraps a real tool for) -- it shells out to `ruff check --select
F401` (this project's own dev-dependency linter, invoked as a subprocess
since ruff is a compiled binary with no importable Python API) and reads
back its own unused-import findings, the same rule that already gates
this project's own CI.

The fix is a real deletion of the whole import statement's line range,
using Finding.fix_is_deletion (fix="" is the delete-these-lines marker,
not "no fix" -- see that field's own docstring in src/core/models.py for
why a plain fix="" can't mean this on its own) rather than reimplementing
ruff's own edit, which it already reports (and marks "safe") in its JSON
output.

Multiple unused names from the SAME `from x import (a, b, c)` statement
share ONE edit spanning the whole statement (confirmed live: ruff
reports one F401 diagnostic per name, but all of them point at the same
edit) -- reported here as diagnostics are grouped by their (start, end)
span first, so removing `a` and `b` together doesn't produce two
findings whose ranges overlap and conflict each other out in
apply_fixes_to_file(); it produces one finding covering the whole
statement, naming every unused import in it.

Fails safe: if the `ruff` subprocess isn't available, times out, or
returns anything unexpected, this returns [] rather than raising --
same "a checker's own failure must never look like a clean pass at the
orchestrator level" principle src/core/orchestrator.py's ReviewStatus
exists to protect elsewhere; this specific checker's own failure simply
means one fewer checker ran, not an analysis-wide false "no issues".
"""
from __future__ import annotations

import json
import subprocess
import sys

from src.core.models import ConfidenceTier, Finding, Severity

_TIMEOUT_SECONDS = 10


def _edit_span(diag: dict) -> tuple[int, int] | None:
    """The (start_line, end_line) 1-indexed inclusive line range ruff's
    own fix would delete, derived from its edit's location/end_location
    (both 1-indexed, end_location being EXCLUSIVE of its own column --
    "row R, column 1" means "up to but not including row R", i.e. the
    deletion actually covers through row R-1). Returns None if the fix
    is missing or doesn't look like a whole-line deletion (an edit with
    non-empty replacement content, or one that doesn't start/end at
    column 1) -- safer to skip a fix than guess at a shape that isn't
    the clean whole-line case this was built for."""
    fix = diag.get("fix")
    if not fix or not fix.get("edits"):
        return None
    edit = fix["edits"][0]
    if edit.get("content", "") != "":
        return None  # not a pure deletion

    start = edit.get("location", {})
    end = edit.get("end_location", {})
    if start.get("column") != 1:
        return None
    start_row = start.get("row")
    end_row = end.get("row")
    end_col = end.get("column")
    if not start_row or not end_row:
        return None
    if end_col == 1:
        end_row -= 1  # exclusive boundary at the start of end_row
    return start_row, end_row


def detect_unused_imports(code: str, filename: str) -> list[Finding]:
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "ruff", "check",
                "--select", "F401",
                "--output-format", "json",
                "--stdin-filename", filename,
                "--no-cache",
                "-",
            ],
            input=code,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        diagnostics = json.loads(result.stdout or "[]")
    except (
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return []

    lines = code.splitlines()
    groups: dict[tuple[int, int], list[str]] = {}
    no_fix: list[tuple[int, str]] = []

    for diag in diagnostics:
        if diag.get("code") != "F401":
            continue
        lineno = diag.get("location", {}).get("row")
        if not lineno or not (0 < lineno <= len(lines)):
            continue

        message = diag.get("message", "")
        name = message.split("`")[1] if "`" in message else "?"

        span = _edit_span(diag)
        if span is None or not (0 < span[0] <= len(lines) and span[1] <= len(lines)):
            no_fix.append((lineno, name))
            continue
        groups.setdefault(span, []).append(name)

    findings: list[Finding] = []
    for (start, end), names in groups.items():
        names_display = ", ".join(f"'{n}'" for n in names)
        findings.append(
            Finding(
                file=filename,
                line=start,
                end_line=end if end > start else 0,
                category="style",
                severity=Severity.INFO,
                message=(
                    f"{names_display} imported but never used anywhere in the "
                    f"file — the whole import statement can be removed."
                ),
                bad_code=lines[start - 1].strip(),
                fix="",
                fix_is_deletion=True,
                confidence=ConfidenceTier.MEDIUM,
                source="unused_import_checker",
            )
        )
    for lineno, name in no_fix:
        findings.append(
            Finding(
                file=filename,
                line=lineno,
                category="style",
                severity=Severity.INFO,
                message=f"'{name}' is imported but never used anywhere in the file.",
                bad_code=lines[lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="unused_import_checker",
            )
        )

    return findings
