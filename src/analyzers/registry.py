"""
src/analyzers/registry.py

Single entry point that runs every deterministic checker in this package,
optionally the Stage 2 LLM supplement, and applies the mandatory trust
check (src/core/grounding.py's is_trustworthy: grounding + fix validity)
to every finding before it leaves the analysis layer. This is the one
place both checks are enforced, so no finding source — deterministic
checker or LLM — can bypass either one.

run_deterministic_checkers() is exposed separately (not just inlined into
run_all_checkers()) because src/core/orchestrator.py needs to run the
deterministic pass and the LLM supplement independently, to observe
whether the LLM call itself succeeded or failed — something
run_all_checkers()'s merged list can't distinguish.

include_llm defaults to False: the deterministic checkers work standalone,
with no API key and no network call, and tests/CI must be able to rely on
that. Callers that want the LLM supplement pass include_llm=True.
"""
from __future__ import annotations

from collections.abc import Callable

from src.analyzers.command_injection_checker import detect_command_injection
from src.analyzers.dict_key_checker import detect_unguarded_dict_access
from src.analyzers.division_guard_checker import detect_unguarded_division
from src.analyzers.file_exists_checker import detect_unguarded_file_open
from src.analyzers.hardcoded_secret_checker import detect_hardcoded_secrets
from src.analyzers.http_timeout_checker import detect_unguarded_http_calls
from src.analyzers.insecure_http_checker import detect_insecure_http_urls
from src.analyzers.resource_leak_checker import detect_unclosed_file_handles
from src.analyzers.sql_injection_checker import detect_sql_injection
from src.analyzers.unsafe_yaml_checker import detect_unsafe_yaml_load
from src.analyzers.unstored_constructor_param_checker import (
    detect_unstored_constructor_params,
)
from src.core.grounding import is_trustworthy
from src.core.models import Finding

Checker = Callable[[str, str], list[Finding]]

CHECKERS: tuple[Checker, ...] = (
    detect_unguarded_dict_access,
    detect_unguarded_division,
    detect_unguarded_file_open,
    detect_unstored_constructor_params,
    detect_unguarded_http_calls,
    detect_hardcoded_secrets,
    detect_sql_injection,
    detect_command_injection,
    detect_unsafe_yaml_load,
    detect_insecure_http_urls,
    detect_unclosed_file_handles,
)


def run_deterministic_checkers(code: str, filename: str) -> list[Finding]:
    findings: list[Finding] = []
    for checker in CHECKERS:
        for finding in checker(code, filename):
            if is_trustworthy(finding, code):
                findings.append(finding)
    return findings


def run_all_checkers(code: str, filename: str, *, include_llm: bool = False) -> list[Finding]:
    findings = run_deterministic_checkers(code, filename)

    if include_llm:
        from src.agents.llm_supplement import get_llm_findings

        for finding in get_llm_findings(code, filename):
            if is_trustworthy(finding, code):
                findings.append(finding)

    return findings
