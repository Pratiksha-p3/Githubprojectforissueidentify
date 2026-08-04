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
one, same as before. A file with three missing colons in a row now gets
all three reported in one pass.

_missing_indent_fix() covers a second SyntaxError shape -- "expected an
indented block after X on line N". CPython's message names the
enclosing header's line, so the target indentation isn't a guess -- it's
exactly one level (4 spaces) deeper than that header. What IS a genuine
guess is how many of the following lines also belong inside the newly-
indented block; there's no parser fact for that. Rather than guess and
hope, this tries reindenting an increasing number of lines (offsetting
each by the same amount, preserving their relative nesting) and asks
the parser itself to confirm: the smallest number of lines that makes
the whole file parse again is what gets returned as the fix, so the fix
is verified to actually resolve the syntax error, not just a plausible-
looking guess. It stays MEDIUM confidence, not HIGH, because "smallest
parsing fix" is a reasonable tie-break, not a certainty about intent --
confirmed with a concrete counter-example: `class C:\ndef f(self): ...\n
def g(self): ...` has more than one syntactically valid reindent (just
`f`, or both `f` and `g`), and the smallest one doesn't always match
what a human meant. The message spells out exactly which lines moved
and why. If no reindent within a bounded forward search parses cleanly,
no fix is offered at all, same as any other unrecognized syntax error.

_misaligned_indent_fix() covers the remaining two indentation
SyntaxError shapes: "unexpected indent" (a line indented MORE than
expected, with no preceding colon-header authorizing a new block --
typically stray extra spaces) and "unindent does not match any outer
indentation level" (a line dedents to a column that isn't any enclosing
block's actual indentation). Neither message names a header line to
anchor to, so instead of one analytically-derived target this tries
every indentation level already used earlier in the file, closest to
the offending line's current indentation first, and -- same parser-as-
oracle approach as _missing_indent_fix() -- returns the first (level,
span) the parser confirms actually fixes the file. Also stays MEDIUM,
never HIGH, for the same reason: "closest level that happens to parse"
is a strong heuristic, not a certainty about what was intended.

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
import re

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


_INDENT_HEADER_LINE = re.compile(r"on line (\d+)$")
_INDENT_SEARCH_WINDOW = 50  # lines forward to try before giving up


def _missing_indent_fix(code: str, e: SyntaxError) -> tuple[str, int, str]:
    """Fix for "expected an indented block after X on line N" -- see the
    module docstring for the full reasoning. Returns (fix, end_line,
    reason) where `fix` spans lines[e.lineno .. end_line] (1-indexed,
    inclusive), or ("", 0, "") if no reindent within the search window
    makes the file parse."""
    if not e.lineno or not e.msg or "expected an indented block" not in e.msg:
        return "", 0, ""
    header_match = _INDENT_HEADER_LINE.search(e.msg)
    if not header_match:
        return "", 0, ""
    header_lineno = int(header_match.group(1))

    lines = code.splitlines()
    if not (0 < e.lineno <= len(lines) and 0 < header_lineno <= len(lines)):
        return "", 0, ""

    header_line = lines[header_lineno - 1]
    first_line = lines[e.lineno - 1]
    if not first_line.strip():
        return "", 0, ""

    header_indent = len(header_line) - len(header_line.lstrip())
    first_indent = len(first_line) - len(first_line.lstrip())
    offset = (header_indent + 4) - first_indent
    if offset <= 0:
        return "", 0, ""

    # Bound the search: stop as soon as a line dedents below even the
    # enclosing header's own level (definitely outside any nested scope
    # the header could own), or after a generous fixed window, whichever
    # comes first.
    max_end_line = e.lineno
    for idx in range(e.lineno, min(len(lines), e.lineno + _INDENT_SEARCH_WINDOW)):
        candidate = lines[idx]
        if candidate.strip() and (len(candidate) - len(candidate.lstrip())) < header_indent:
            break
        max_end_line = idx + 1

    for end_line in range(e.lineno, max_end_line + 1):
        patched_lines = list(lines)
        for idx in range(e.lineno - 1, end_line):
            if patched_lines[idx].strip():
                patched_lines[idx] = " " * offset + patched_lines[idx]
        try:
            ast.parse("\n".join(patched_lines))
        except SyntaxError:
            continue

        fix = "\n".join(patched_lines[e.lineno - 1 : end_line])
        span = "that line" if end_line == e.lineno else f"lines {e.lineno}-{end_line}"
        reason = (
            f"'{header_line.strip()}' on line {header_lineno} needs its body "
            f"indented one level deeper (column {header_indent + 4}), but line "
            f"{e.lineno} sits at column {first_indent} -- shifting {span} right "
            f"by {offset} space(s) is the smallest change that makes the file "
            f"parse again."
        )
        return fix, end_line, reason

    return "", 0, ""


_MISALIGNED_INDENT_MESSAGES = (
    "unexpected indent",
    "unindent does not match any outer indentation level",
)


def _misaligned_indent_fix(code: str, e: SyntaxError) -> tuple[str, int, str]:
    """Fix for CPython's other two indentation SyntaxError shapes:
    "unexpected indent" (a line is indented MORE than the parser expects,
    with no preceding colon-header authorizing a new block -- typically
    stray extra spaces) and "unindent does not match any outer
    indentation level" (a line dedents to a column that isn't any
    enclosing block's actual indentation).

    Unlike _missing_indent_fix(), CPython's message doesn't name a
    header line here -- there's no single fact to anchor the target
    indentation to. Instead this tries every indentation level already
    used earlier in the file (the levels that are actually meaningful in
    THIS file, whatever its indent width), closest to the offending
    line's current indentation first, and -- same as
    _missing_indent_fix() -- asks the parser to confirm: the first
    (level, span) combination that makes the whole file parse again is
    what gets returned. Also tries `first_indent + 4` as a candidate in
    case the file has no earlier line at the right level yet (e.g. the
    very first statement in a function body)."""
    if not e.lineno or not e.msg or e.msg not in _MISALIGNED_INDENT_MESSAGES:
        return "", 0, ""
    lines = code.splitlines()
    if not (0 < e.lineno <= len(lines)):
        return "", 0, ""
    first_line = lines[e.lineno - 1]
    if not first_line.strip():
        return "", 0, ""
    first_indent = len(first_line) - len(first_line.lstrip())

    existing_levels = {0, first_indent + 4}
    for line in lines[: e.lineno - 1]:
        if line.strip():
            existing_levels.add(len(line) - len(line.lstrip()))
    candidates = sorted(
        (lvl for lvl in existing_levels if lvl != first_indent),
        key=lambda lvl: (abs(lvl - first_indent), lvl),
    )

    max_end_line = min(len(lines), e.lineno + _INDENT_SEARCH_WINDOW)

    for target in candidates:
        offset = target - first_indent
        for end_line in range(e.lineno, max_end_line + 1):
            patched_lines = list(lines)
            underflow = False
            for idx in range(e.lineno - 1, end_line):
                line = patched_lines[idx]
                if not line.strip():
                    continue
                cur_indent = len(line) - len(line.lstrip())
                new_indent = cur_indent + offset
                if new_indent < 0:
                    underflow = True
                    break
                patched_lines[idx] = " " * new_indent + line.lstrip()
            if underflow:
                break  # shrinking further only underflows more -- stop growing this span
            try:
                ast.parse("\n".join(patched_lines))
            except SyntaxError:
                continue

            fix = "\n".join(patched_lines[e.lineno - 1 : end_line])
            span = "that line" if end_line == e.lineno else f"lines {e.lineno}-{end_line}"
            direction = "deeper" if offset > 0 else "shallower"
            reason = (
                f"line {e.lineno} sits at column {first_indent}, which doesn't "
                f"match any indentation level already used earlier in the file "
                f"at that point -- column {target} ({abs(offset)} space(s) "
                f"{direction}) is the closest level that makes {span} parse "
                f"again."
            )
            return fix, end_line, reason

    return "", 0, ""


def _syntax_error_fix(code: str, e: SyntaxError) -> tuple[str, int, ConfidenceTier, str]:
    """Tries each narrow, purpose-built SyntaxError fix in turn and
    returns (fix, end_line, confidence, reason). The colon fix is
    checked first because it's the only shape unambiguous enough to
    safely chain the iterative collector below past it."""
    colon_fix = _missing_colon_fix(code, e)
    if colon_fix:
        return colon_fix, 0, ConfidenceTier.HIGH, ""

    indent_fix, indent_end_line, indent_reason = _missing_indent_fix(code, e)
    if indent_fix:
        return indent_fix, indent_end_line, ConfidenceTier.MEDIUM, indent_reason

    misaligned_fix, misaligned_end_line, misaligned_reason = _misaligned_indent_fix(code, e)
    if misaligned_fix:
        return misaligned_fix, misaligned_end_line, ConfidenceTier.MEDIUM, misaligned_reason

    return "", 0, ConfidenceTier.MEDIUM, ""


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
            fix, end_line, confidence, reason = _syntax_error_fix(working_code, e)
            bad_code = (
                working_lines[e.lineno - 1].strip()
                if e.lineno and 0 < e.lineno <= len(working_lines)
                else ""
            )
            message = f"File does not parse as valid Python: {e.msg}"
            if reason:
                message += f" ({reason})"
            findings.append(
                Finding(
                    file=filename,
                    line=e.lineno or 1,
                    end_line=end_line,
                    category="syntax",
                    severity=Severity.CRITICAL,
                    message=message,
                    bad_code=bad_code,
                    fix=fix,
                    confidence=confidence,
                    source="orchestrator",
                )
            )
            # Only the HIGH-confidence colon fix is unambiguous enough to
            # chain the search further into the file on top of -- a MEDIUM
            # guess (or no fix at all) means stop and report just this one.
            if confidence != ConfidenceTier.HIGH or not fix or not e.lineno:
                break
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
