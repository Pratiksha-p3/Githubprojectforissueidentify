"""
src/agents/llm_supplement.py

An LLM pass that supplements the deterministic checkers in src/analyzers/
for runtime/logic bugs outside their fixed shapes — the same
deterministic-first, LLM-supplement architecture that let real bugs get
caught throughout the previous implementation's lifetime even when the
LLM API was down or rate-limited.

As of Stage 11, the actual call/parse/validate logic lives in
src/agents/_llm_finding_agent.py, shared with the other specialized
agents in this package (security_agent.py, style_agent.py,
test_coverage_agent.py) — this module supplies only its own prompt and
valid categories. Findings are NOT trust-checked here; that happens
once, centrally, wherever they're consumed (src/analyzers/registry.py,
src/core/orchestrator.py, src/agents/coordinator.py), via
src/core/grounding.py's is_trustworthy().

get_llm_findings_with_status() exists alongside the simpler
get_llm_findings() because "zero findings" is ambiguous on its own — it
means either "the LLM ran and found nothing" or "the LLM call itself
failed", and callers that need an honest ReviewStatus (DEGRADED vs.
COMPLETED) need to tell those apart rather than silently treating a
failed call as a clean pass.
"""
from __future__ import annotations

from src.agents._llm_finding_agent import run_finding_agent
from src.core.models import Finding

_SYSTEM_PROMPT = (
    "You are a senior software engineer with 20 years of production experience "
    "doing a line-by-line code review. You are thorough and pragmatic: you catch "
    "real bugs, not style nitpicks. Return JSON only, no markdown fences, no prose."
)

_TASK_PROMPT = """Review this code the way a senior engineer would: read it line by line
and find every RUNTIME error and every LOGIC error you can, no matter what
shape they take. Do not limit yourself to a fixed checklist.

Do NOT report syntax errors or security vulnerabilities — those are
handled elsewhere."""

_VALID_CATEGORIES = {"runtime", "logic"}


def get_llm_findings(code: str, filename: str, *, context: str = "") -> list[Finding]:
    """
    Best-effort supplement to the deterministic checks — never a hard
    dependency. An empty list here means "the LLM found nothing" or "the
    LLM call failed"; those are deliberately indistinguishable through
    this simpler entry point. Callers that need to tell them apart (e.g.
    src/core/orchestrator.py, to set an honest ReviewStatus) should use
    get_llm_findings_with_status() instead.

    `context` is pre-formatted "similar existing code elsewhere in the
    repo" text (src/rag/retriever.py's format_context_for_prompt()) —
    optional, since Stage 6's RAG index isn't a hard dependency for a
    review to run at all.
    """
    findings, _succeeded = get_llm_findings_with_status(code, filename, context=context)
    return findings


def get_llm_findings_with_status(
    code: str, filename: str, *, context: str = ""
) -> tuple[list[Finding], bool]:
    """
    Same as get_llm_findings(), but also returns whether the LLM pass
    actually completed — False if the API call raised, or if the
    response couldn't be parsed as the expected JSON shape at all. A
    successful call that genuinely found nothing still returns ([], True).
    """
    return run_finding_agent(
        code,
        filename,
        system_prompt=_SYSTEM_PROMPT,
        task_prompt=_TASK_PROMPT,
        valid_categories=_VALID_CATEGORIES,
        source_name="llm_supplement",
        context=context,
    )
