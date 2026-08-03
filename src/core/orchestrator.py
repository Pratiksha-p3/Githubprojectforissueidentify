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

_missing_colon_fix() generates a fix for exactly one narrow SyntaxError
shape: CPython's own "expected ':'" (a compound statement header --
if/elif/else/for/while/def/class/try/except/finally/with -- missing its
colon). Every other checker's fix in this project is capped at MEDIUM
confidence because which exact remediation is "correct" is a judgment
call about intent (see src/core/confidence.py) -- this one genuinely
isn't: CPython's own parser has already told us exactly where the colon
belongs, there's no ambiguity about intent to guess at, so it's the
first fix in this project confident enough to mark ConfidenceTier.HIGH.
Skipped (no fix offered, same as any other syntax error) when the line
has a trailing comment, since inserting a colon after a `#` would land
inside the comment and not actually fix anything.

_collect_syntax_error_findings() handles a file with MORE THAN ONE
syntax error: Python's parser fundamentally cannot see past a syntax
error it doesn't know how to resolve, so there is no way to discover a
second error without first getting past the first one. Where the first
error IS the unambiguous missing-colon shape, this applies that fix to
an in-memory working copy (never the caller's real file) and re-parses,
repeating until either the file parses clean or it hits a syntax error
that isn't the colon shape — at which point it stops and reports that
one with no fix, same as before. A file with three missing colons in a
row now gets all three reported in one pass; a file whose first syntax
error is genuinely ambiguous (e.g. "expected an indented block") still
only reveals that one error, because there is no way to guess past it.

use_multi_agent (Stage 11, opt-in, default False) swaps the single
runtime/logic LLM supplement for src/agents/coordinator.py's four
specialized agents (runtime/logic, security, style, test_coverage).
Off by default because it multiplies LLM calls per file 4x — a real
cost/rate-limit concern, not something every review should pay for
without an explicit choice to do so.

Stage 14's canary prompt rollout (src/core/canary.py) is wired in here
using f"{repo}:{commit_sha}" as the routing key, so the same commit
always resolves to the same model variant even across a Celery retry —
on both the single-agent path (get_llm_findings_with_status) and the
multi-agent path (run_all_agents), which threads the same key through
all four specialized agents so a given review resolves to one variant
consistently, not a mix of stable and canary across agents.
"""
from __future__ import annotations

import ast

from src.agents.coordinator import run_all_agents
from src.agents.llm_supplement import get_llm_findings_with_status
from src.analyzers.registry import run_deterministic_checkers
from src.core.grounding import is_trustworthy
from src.core.models import ConfidenceTier, Finding, ReviewResult, ReviewStatus, Severity


def _missing_colon_fix(code: str, e: SyntaxError) -> str:
    if e.msg != "expected ':'" or not e.lineno:
        return ""
    lines = code.splitlines()
    if not (0 < e.lineno <= len(lines)):
        return ""
    line = lines[e.lineno - 1]
    if "#" in line or line.rstrip().endswith(":"):
        return ""
    return f"{line.rstrip()}:"


_MAX_SYNTAX_ERRORS_PER_REVIEW = 25  # safety cap, not a realistic file's actual count


def _collect_syntax_error_findings(code: str, filename: str) -> list[Finding]:
    findings: list[Finding] = []
    working_lines = code.splitlines()

    for _ in range(_MAX_SYNTAX_ERRORS_PER_REVIEW):
        working_code = "\n".join(working_lines)
        try:
            ast.parse(working_code)
            break  # every error found so far was auto-fixed; file now parses
        except SyntaxError as e:
            fix = _missing_colon_fix(working_code, e)
            bad_code = (
                working_lines[e.lineno - 1].strip()
                if e.lineno and 0 < e.lineno <= len(working_lines)
                else ""
            )
            findings.append(
                Finding(
                    file=filename,
                    line=e.lineno or 1,
                    category="syntax",
                    severity=Severity.CRITICAL,
                    message=f"File does not parse as valid Python: {e.msg}",
                    bad_code=bad_code,
                    fix=fix,
                    confidence=ConfidenceTier.HIGH if fix else ConfidenceTier.MEDIUM,
                    source="orchestrator",
                )
            )
            if not fix or not e.lineno:
                break  # can't guess past a non-colon error -- stop here
            working_lines[e.lineno - 1] = fix

    return findings


def review_code(
    code: str,
    filename: str,
    *,
    repo: str,
    commit_sha: str,
    include_llm: bool = True,
    use_multi_agent: bool = False,
) -> ReviewResult:
    try:
        ast.parse(code)
    except SyntaxError as e:
        syntax_findings = _collect_syntax_error_findings(code, filename)
        return ReviewResult(
            repo=repo,
            commit_sha=commit_sha,
            status=ReviewStatus.FAILED,
            findings=syntax_findings,
            summary=(
                f"Analysis could not run — {len(syntax_findings)} syntax error(s) "
                f"found, starting with: {e.msg}"
            ),
        )

    findings = run_deterministic_checkers(code, filename)
    status = ReviewStatus.COMPLETED

    if include_llm:
        if use_multi_agent:
            llm_findings, llm_succeeded = run_all_agents(
                code, filename, canary_key=f"{repo}:{commit_sha}"
            )
        else:
            llm_findings, llm_succeeded = get_llm_findings_with_status(
                code, filename, canary_key=f"{repo}:{commit_sha}"
            )
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
