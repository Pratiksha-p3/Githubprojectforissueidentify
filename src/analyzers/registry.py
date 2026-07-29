"""
src/analyzers/registry.py

Single entry point that runs every deterministic checker in this package,
optionally the Stage 2 LLM supplement, and applies two mandatory sanity
checks to every finding before it leaves the analysis layer — grounding
(src/core/grounding.py: does bad_code actually match the real file?) and
fix validity (does the proposed fix even parse as valid Python?). This is
the one place both checks are enforced, so no finding source — deterministic
checker or LLM — can bypass either one.

include_llm defaults to False: the deterministic checkers work standalone,
with no API key and no network call, and tests/CI must be able to rely on
that. Callers that want the LLM supplement (Stage 3's orchestrator) pass
include_llm=True explicitly.
"""
from __future__ import annotations

import ast
from collections.abc import Callable

from src.analyzers.dict_key_checker import detect_unguarded_dict_access
from src.analyzers.division_guard_checker import detect_unguarded_division
from src.analyzers.file_exists_checker import detect_unguarded_file_open
from src.analyzers.http_timeout_checker import detect_unguarded_http_calls
from src.analyzers.unstored_constructor_param_checker import (
    detect_unstored_constructor_params,
)
from src.core.grounding import is_grounded
from src.core.models import Finding

Checker = Callable[[str, str], list[Finding]]

CHECKERS: tuple[Checker, ...] = (
    detect_unguarded_dict_access,
    detect_unguarded_division,
    detect_unguarded_file_open,
    detect_unstored_constructor_params,
    detect_unguarded_http_calls,
)


def _is_valid_fix(fix: str) -> bool:
    """True if `fix` parses as valid standalone Python. Wraps in a dummy
    block first if it looks pre-indented, since a fix's own indentation
    reflects where it belongs in the file, not module level."""
    if not fix.strip():
        return True  # nothing proposed — nothing to invalidate
    first_line = fix.splitlines()[0]
    try:
        wrapped = f"if True:\n{fix}" if first_line[:1] in (" ", "\t") else fix
        ast.parse(wrapped)
        return True
    except SyntaxError:
        return False


def _passes_sanity_checks(finding: Finding, code: str) -> bool:
    return is_grounded(finding, code) and _is_valid_fix(finding.fix)


def run_all_checkers(code: str, filename: str, *, include_llm: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for checker in CHECKERS:
        for finding in checker(code, filename):
            if _passes_sanity_checks(finding, code):
                findings.append(finding)

    if include_llm:
        from src.agents.llm_supplement import get_llm_findings

        for finding in get_llm_findings(code, filename):
            if _passes_sanity_checks(finding, code):
                findings.append(finding)

    return findings
