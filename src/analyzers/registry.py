"""
src/analyzers/registry.py

Single entry point that runs every checker in this package and applies the
mandatory grounding check (src/core/grounding.py) to every finding before
it leaves the analysis layer — deterministic checkers included. That's
deliberate: this is the one place a Stage 2 LLM-supplement pass will also
plug into later, so grounding is enforced by the registry itself, not by
each individual source remembering to call it.
"""
from __future__ import annotations

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


def run_all_checkers(code: str, filename: str) -> list[Finding]:
    findings: list[Finding] = []
    for checker in CHECKERS:
        for finding in checker(code, filename):
            if is_grounded(finding, code):
                findings.append(finding)
    return findings
