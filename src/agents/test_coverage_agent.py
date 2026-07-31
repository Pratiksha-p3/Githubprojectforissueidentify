"""
src/agents/test_coverage_agent.py

LLM pass specialized for identifying missing test coverage — functions
or branches with no apparent corresponding test, and important edge
cases (empty input, None, zero, boundary values, error paths) that look
untested. Severity is always capped at INFO — a missing test is a gap
worth flagging, not a merge-blocking defect the way a security or
correctness bug is.
"""
from __future__ import annotations

from src.agents._llm_finding_agent import run_finding_agent
from src.core.models import Finding, Severity

_SYSTEM_PROMPT = (
    "You are a senior software engineer reviewing test coverage. You are "
    "pragmatic: you flag genuinely untested important logic, not every "
    "possible edge case. Return JSON only, no markdown fences, no prose."
)

_TASK_PROMPT = """Review this code for TEST COVERAGE gaps only: functions or branches with
no apparent test, and important edge cases (empty input, None, zero,
boundary values, error paths) that look untested. Do not report
security, style, or general logic/runtime bugs — those are handled
elsewhere."""

_VALID_CATEGORIES = {"test_coverage"}


def get_test_coverage_findings(code: str, filename: str, *, context: str = "") -> list[Finding]:
    findings, _succeeded = get_test_coverage_findings_with_status(
        code, filename, context=context
    )
    return findings


def get_test_coverage_findings_with_status(
    code: str, filename: str, *, context: str = "", canary_key: str | None = None
) -> tuple[list[Finding], bool]:
    findings, succeeded = run_finding_agent(
        code,
        filename,
        system_prompt=_SYSTEM_PROMPT,
        task_prompt=_TASK_PROMPT,
        valid_categories=_VALID_CATEGORIES,
        source_name="test_coverage_agent",
        context=context,
        canary_key=canary_key,
    )
    return [_cap_at_info(f) for f in findings], succeeded


def _cap_at_info(finding: Finding) -> Finding:
    if finding.severity != Severity.INFO:
        return finding.model_copy(update={"severity": Severity.INFO})
    return finding
