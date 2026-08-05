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

No fix is generated, even though ruff's own JSON output includes one
(and marks it "safe"): ruff's fix deletes the WHOLE LINE, but this
project's Finding model has no way to express "replace this line with
NOTHING" -- Finding.fix == "" is the exact sentinel every other part of
the codebase (apply_fixes_to_file, post_fix_suggestions,
manual_review_reason, ...) already uses to mean "no fix was generated
at all". Reusing it for "the fix is to delete this line" would silently
collide with that meaning everywhere else a Finding's fix is checked.
Rather than change that shared sentinel for one checker, this stays
detection-only -- the message alone ("`os` imported but unused, line N")
is already fully actionable for a human to act on directly, the same
"detection over a risky auto-rewrite" choice
src/analyzers/sql_injection_checker.py and others already make for a
different reason (there, the correct fix needs context; here, the
model just doesn't have a way to say "delete" yet).

Fails safe: if the `ruff` subprocess isn't available, times out, or
returns anything unexpected, this returns [] rather than raising --
same "a checker's own failure must never look like a clean pass at the
orchestrator level" principle src/core/orchestrator.py's ReviewStatus
exists to protect elsewhere; this specific checker's own failure simply
means one fewer checker ran, not a analysis-wide false "no issues".
"""
from __future__ import annotations

import json
import subprocess
import sys

from src.core.models import ConfidenceTier, Finding, Severity

_TIMEOUT_SECONDS = 10


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
    findings: list[Finding] = []
    for diag in diagnostics:
        if diag.get("code") != "F401":
            continue
        lineno = diag.get("location", {}).get("row")
        if not lineno or not (0 < lineno <= len(lines)):
            continue

        message = diag.get("message", "")
        name = message.split("`")[1] if "`" in message else "?"
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
