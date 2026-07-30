"""
src/tools/semgrep_runner.py

Shells out to the semgrep CLI and parses its JSON output into Finding
objects tagged source="semgrep" — a second, independent static analyzer
alongside src/analyzers/'s AST-based checkers, catching vulnerability
patterns those checkers were never written for (semgrep ships hundreds
of community rules; ours are a handful of specific, proven shapes).

Requires the `semgrep` CLI on PATH (`pip install semgrep`, or the
standalone binary — documented in README.md, no account needed). This
module never tries to install it, and returns [] with a clear log line
if it's missing — the same "best-effort supplement, never a hard
dependency" pattern src/agents/llm_supplement.py uses for the LLM pass.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from src.core.models import ConfidenceTier, Finding, Severity

_SEVERITY_MAP = {
    "ERROR": Severity.CRITICAL,
    "WARNING": Severity.WARNING,
    "INFO": Severity.INFO,
}

# Confirmed against a real, unauthenticated `semgrep --config auto` run:
# for registry rules gated behind a login, semgrep puts the literal
# string "requires login" into extra.lines instead of the matched code
# — not an edge case, this is normal behavior for the common case of
# running semgrep without `semgrep login` first. Passing that through
# as bad_code would make src/core/grounding.py's is_trustworthy() check
# silently discard an otherwise-real, valid finding, since "requires
# login" never appears in the actual file.
_UNAVAILABLE_LINES_PLACEHOLDER = "requires login"


def is_semgrep_available() -> bool:
    return shutil.which("semgrep") is not None


def scan_file(filepath: str, *, config: str = "auto", timeout_seconds: int = 60) -> list[Finding]:
    if not is_semgrep_available():
        print("[semgrep_runner] semgrep CLI not found on PATH — skipping (pip install semgrep)")
        return []

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted interpolation
            ["semgrep", "--config", config, "--json", "--quiet", filepath],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"[semgrep_runner] semgrep timed out after {timeout_seconds}s on {filepath}")
        return []
    except OSError as e:
        print(f"[semgrep_runner] semgrep failed to run: {e}")
        return []

    if not proc.stdout.strip():
        return []

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("[semgrep_runner] Could not parse semgrep output as JSON")
        return []

    return [_to_finding(r) for r in data.get("results", [])]


def _to_finding(result: dict) -> Finding:
    extra = result.get("extra", {})
    severity = _SEVERITY_MAP.get(extra.get("severity", "WARNING"), Severity.WARNING)
    start = result.get("start", {})

    raw_lines = extra.get("lines", "")
    bad_code = "" if raw_lines == _UNAVAILABLE_LINES_PLACEHOLDER else raw_lines

    return Finding(
        file=result.get("path", ""),
        line=start.get("line", 0),
        category="security",
        severity=severity,
        message=extra.get("message", result.get("check_id", "semgrep finding")),
        bad_code=bad_code,
        confidence=ConfidenceTier.MEDIUM,
        source="semgrep",
    )
