"""
src/agents/security_agent.py

LLM pass specialized for SECURITY findings — injection, hardcoded
secrets, insecure cryptography, SSRF, path traversal, missing
authorization checks, and similar — outside what src/tools/
semgrep_runner.py's static rules catch. Part of Stage 11's multi-agent
specialization: same deterministic-first, LLM-supplement architecture
as src/agents/llm_supplement.py (runtime/logic), just narrower-focused,
sharing the same underlying call/parse/validate logic
(src/agents/_llm_finding_agent.py).
"""
from __future__ import annotations

from src.agents._llm_finding_agent import run_finding_agent
from src.core.models import Finding

_SYSTEM_PROMPT = (
    "You are a senior application security engineer doing a line-by-line "
    "security review. You are thorough and pragmatic: you catch real "
    "vulnerabilities, not theoretical ones. Return JSON only, no markdown "
    "fences, no prose."
)

_TASK_PROMPT = """Review this code for SECURITY vulnerabilities only: injection (SQL,
command, code), hardcoded secrets/credentials, insecure cryptography,
path traversal, SSRF, insecure deserialization, missing authorization
checks, and similar. Do not report style issues, missing tests, or
generic logic/runtime bugs — those are handled elsewhere."""

_VALID_CATEGORIES = {"security"}


def get_security_findings(code: str, filename: str, *, context: str = "") -> list[Finding]:
    findings, _succeeded = get_security_findings_with_status(code, filename, context=context)
    return findings


def get_security_findings_with_status(
    code: str, filename: str, *, context: str = "", canary_key: str | None = None
) -> tuple[list[Finding], bool]:
    return run_finding_agent(
        code,
        filename,
        system_prompt=_SYSTEM_PROMPT,
        task_prompt=_TASK_PROMPT,
        valid_categories=_VALID_CATEGORIES,
        source_name="security_agent",
        context=context,
        canary_key=canary_key,
    )
