"""
src/core/orchestrator.py

Runs the full analysis pipeline (deterministic checkers + optional LLM
supplement) against a single file's content and produces a ReviewResult
whose `status` honestly reflects what actually ran — the integration
point where the old system's real, previously-shipped bug (a failed/
rate-limited LLM call producing a result indistinguishable from "clean")
is designed against structurally, not patched on after the fact.

Three outcomes, matching ReviewStatus:
  FAILED    — the file doesn't even parse as valid Python, so the
              deterministic checkers couldn't meaningfully run at all.
  DEGRADED  — deterministic checks ran fine, but the LLM supplement was
              requested and didn't complete (rate limited, API error,
              unparseable response) — zero LLM findings here means
              "unknown", not "clean".
  COMPLETED — everything that was asked for actually ran to completion.

A syntax error is deliberately caught here rather than left to each
checker to quietly swallow on its own (every checker in src/analyzers/
already catches SyntaxError internally and returns []) — otherwise a file
that doesn't even parse would look identical to "reviewed everything,
found 0 issues", which is the exact same class of bug ReviewStatus exists
to prevent, just one level earlier.
"""
from __future__ import annotations

import ast

from src.agents.llm_supplement import get_llm_findings_with_status
from src.analyzers.registry import run_deterministic_checkers
from src.core.grounding import is_trustworthy
from src.core.models import Finding, ReviewResult, ReviewStatus, Severity


def review_code(
    code: str,
    filename: str,
    *,
    repo: str,
    commit_sha: str,
    include_llm: bool = True,
) -> ReviewResult:
    try:
        ast.parse(code)
    except SyntaxError as e:
        return ReviewResult(
            repo=repo,
            commit_sha=commit_sha,
            status=ReviewStatus.FAILED,
            findings=[
                Finding(
                    file=filename,
                    line=e.lineno or 1,
                    category="syntax",
                    severity=Severity.CRITICAL,
                    message=f"File does not parse as valid Python: {e.msg}",
                    source="orchestrator",
                )
            ],
            summary=f"Analysis could not run — syntax error: {e.msg}",
        )

    findings = run_deterministic_checkers(code, filename)
    status = ReviewStatus.COMPLETED

    if include_llm:
        llm_findings, llm_succeeded = get_llm_findings_with_status(code, filename)
        findings.extend(f for f in llm_findings if is_trustworthy(f, code))
        if not llm_succeeded:
            status = ReviewStatus.DEGRADED

    return ReviewResult(
        repo=repo,
        commit_sha=commit_sha,
        status=status,
        findings=findings,
        summary=_build_summary(findings, status),
    )


def _build_summary(findings: list[Finding], status: ReviewStatus) -> str:
    if status != ReviewStatus.COMPLETED:
        return (
            f"Review {status.value} — {len(findings)} finding(s) from the "
            f"part of the analysis that did complete."
        )
    if not findings:
        return "No issues found."
    critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    return f"{len(findings)} issue(s) found ({critical} critical)."
