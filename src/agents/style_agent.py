"""
src/agents/style_agent.py

LLM pass specialized for STYLE/convention findings — unclear naming,
overly complex functions, dead code, inconsistent formatting patterns a
linter wouldn't catch. Severity is always capped at WARNING regardless
of what the model returns — a style issue is never CRITICAL, and this
agent enforces that itself rather than trusting the model's own
severity judgment, so a naming nitpick can never block a merge the way
a real correctness/security finding does.
"""
from __future__ import annotations

from src.agents._llm_finding_agent import run_finding_agent
from src.core.models import Finding, Severity

_SYSTEM_PROMPT = (
    "You are a senior software engineer reviewing code style and conventions. "
    "You are pragmatic: you flag real readability/maintainability problems, "
    "not personal preferences. Return JSON only, no markdown fences, no prose."
)

_TASK_PROMPT = """Review this code for STYLE and convention issues only: unclear naming,
overly complex functions, dead/unreachable code, inconsistent formatting
patterns within the file. Do not report security, correctness, or test
coverage issues — those are handled elsewhere."""

_VALID_CATEGORIES = {"style"}


def get_style_findings(code: str, filename: str, *, context: str = "") -> list[Finding]:
    findings, _succeeded = get_style_findings_with_status(code, filename, context=context)
    return findings


def get_style_findings_with_status(
    code: str, filename: str, *, context: str = ""
) -> tuple[list[Finding], bool]:
    findings, succeeded = run_finding_agent(
        code,
        filename,
        system_prompt=_SYSTEM_PROMPT,
        task_prompt=_TASK_PROMPT,
        valid_categories=_VALID_CATEGORIES,
        source_name="style_agent",
        context=context,
    )
    return [_cap_at_warning(f) for f in findings], succeeded


def _cap_at_warning(finding: Finding) -> Finding:
    if finding.severity == Severity.CRITICAL:
        return finding.model_copy(update={"severity": Severity.WARNING})
    return finding
