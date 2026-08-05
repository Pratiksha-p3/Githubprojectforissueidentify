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

The "does this even parse" gate uses compile(code, filename, "exec"),
NOT ast.parse(code) -- confirmed live that this is not a cosmetic choice.
ast.parse() only does grammar-level parsing; it silently accepts code a
real Python run would reject: a bare `return`/`yield` outside a function,
`break`/`continue` outside a loop, a `nonlocal` with no enclosing binding
-- these are caught by compile()'s symbol-table pass, which ast.parse()
never runs. Confirmed concretely: a file mangled by conflicting "Apply
suggestion" clicks (stray top-level `return` statements left over from a
function whose `def` line got deleted) passed ast.parse() cleanly and
was reviewed as COMPLETED, deterministic checkers and all, while being
completely broken -- unrunnable, and structurally incoherent enough that
several checkers' own pattern matching silently found nothing either,
since the functions they were looking for no longer existed as such.
compile() still just raises SyntaxError (same type, same .lineno/.msg
attributes) for every case ast.parse() already caught, so this is a
strict superset, not a behavior change for anything that already worked
-- every internal verification/retry ast.parse() call in this module
(the "does this candidate fix actually resolve it" checks in
_missing_indent_fix()/_misaligned_indent_fix()) was upgraded to
compile() too, for the same reason.

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
indented block; there's no parser fact for that. This reindents an
increasing number of lines (offsetting each by the same amount,
preserving their relative nesting), bounded by _looks_like_span_boundary()
stopping the span before a sibling def/class/decorator, or a bare
return/break/continue/raise, at the offending line's own indent, and --
within that bound -- prefers the LARGEST span
the parser confirms actually fixes the file, not the smallest. That
preference is deliberate, and was a real live bug before it was added:
"smallest span that compiles" isn't a safe enough bar, because a
too-small span can still be syntactically valid Python on its own --
confirmed concretely with a while-loop whose decrement and return
statements got left OUTSIDE the loop body (a plain sibling statement
after a compound one is valid syntax), silently turning a one-line fix
into an infinite loop at runtime despite "compiling fine". Every line up
through the bound was written at (or nested under) the SAME wrong
indentation as the first offending line, which is strong evidence they
were meant to move together; the new-definition boundary exists
specifically so this doesn't overcorrect into swallowing a genuine
sibling declaration (confirmed against the opposite counter-example:
`class C:\ndef f(self): ...\ndef g(self): ...` -- `g` sits at the exact
same indent as `f` and starts with `def`, so the span stops before it).
It stays MEDIUM confidence, not HIGH, because even with this boundary,
"largest span that parses" is a strong heuristic, not a certainty about
intent. If no reindent within a bounded forward search parses cleanly,
no fix is offered at all, same as any other unrecognized syntax error.

_misaligned_indent_fix() covers the remaining two indentation
SyntaxError shapes: "unexpected indent" (a line indented MORE than
expected, with no preceding colon-header authorizing a new block --
typically stray extra spaces) and "unindent does not match any outer
indentation level" (a line dedents to a column that isn't any enclosing
block's actual indentation). Neither message names a header line to
anchor to, so instead of one analytically-derived target this tries
every indentation level already used earlier in the file, closest to
the offending line's current indentation first -- and, for each level,
the same largest-span-first search (with the same new-definition
boundary) as _missing_indent_fix(), for the same reason. Also stays
MEDIUM, never HIGH: "closest level, largest span that happens to parse"
is a strong heuristic, not a certainty about what was intended.

_misaligned_indent_backward_fix() covers a shape _misaligned_indent_fix()
can't: where the SyntaxError line itself is actually fine, and an
earlier sibling statement immediately above it was the one over-
indented -- CPython reports the error where the dedent doesn't match
(the line AFTER the mistake), not on the mistake itself. Confirmed live
against a real PR file: `total = 0` written one level too deep,
followed by a correctly-indented `for` loop; every forward-search
candidate in _misaligned_indent_fix() failed to parse (because the
REAL fix is behind the error line, not at or after it), so it reported
the syntax error with no fix at all. This walks backward from the
error line instead, collecting the contiguous run of lines immediately
above it that share one indentation level deeper than the error line,
and reindents that run down to match -- confirmed this produces the
exact correct fix for the live case. Tried last, only after every
forward-search option has failed, since "the line after the error is
correct" is the less common shape.

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
_NEW_DEFINITION = re.compile(r"^\s*(async\s+def\b|def\b|class\b|@)")
_BLOCK_EXIT = re.compile(r"^\s*(return|break|continue|raise)\b")


def _looks_like_span_boundary(line: str) -> bool:
    """True for a line that should end a reindent span rather than be
    swallowed into it by the "prefer largest span" search below -- either
    a new sibling def/class/decorator, or a bare return/break/continue/
    raise. Both are common as the very NEXT line after a block at the
    same (wrong) indentation, yet are much more often meant to run AFTER
    the block than as part of it: a loop's final accumulated-result
    return, an if/elif chain's fallback return, a sibling method
    declaration. Confirmed live as two separate real bugs without this
    split: (1) a while-loop's decrement statement left OUTSIDE the loop
    (an earlier, too-small-span version of this fix) was still
    syntactically valid Python, silently becoming an infinite loop; (2) a
    for-loop's trailing `return total` pulled INTO the loop (an
    over-corrected, too-large-span version) returned after the first
    item instead of the whole accumulated sum. Splitting the two DOES
    matter: a plain statement like `total += n` almost always belongs
    inside the block (include it, growing the span); a def/class/return/
    break/continue/raise almost always belongs after it (stop before
    it) -- confirmed against both directions with concrete cases in
    tests/test_orchestrator.py."""
    return bool(_NEW_DEFINITION.match(line) or _BLOCK_EXIT.match(line))


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
    # the header could own), a span-boundary line appears at the
    # offending line's own indent (see _looks_like_span_boundary
    # docstring), or after a generous fixed window -- whichever comes
    # first.
    max_end_line = e.lineno
    for idx in range(e.lineno, min(len(lines), e.lineno + _INDENT_SEARCH_WINDOW)):
        candidate = lines[idx]
        if not candidate.strip():
            max_end_line = idx + 1
            continue
        candidate_indent = len(candidate) - len(candidate.lstrip())
        if candidate_indent < header_indent:
            break
        if candidate_indent == first_indent and _looks_like_span_boundary(candidate):
            break
        max_end_line = idx + 1

    # Prefer the LARGEST span the parser confirms fixes the file, not the
    # smallest -- confirmed live that "smallest that compiles" isn't a
    # safe enough bar: a while-loop's decrement/return left outside the
    # loop body is still syntactically valid Python (a sibling statement
    # after the loop), just an infinite loop at runtime. Every line up
    # through max_end_line was already written at the SAME wrong
    # indentation as the first offending line (or nested deeper within
    # it), which is strong evidence they were all meant to move together
    # -- the smaller spans this now only falls back to are for cases
    # where the larger one doesn't even parse.
    for end_line in range(max_end_line, e.lineno - 1, -1):
        patched_lines = list(lines)
        for idx in range(e.lineno - 1, end_line):
            if patched_lines[idx].strip():
                patched_lines[idx] = " " * offset + patched_lines[idx]
        try:
            compile("\n".join(patched_lines), "<string>", "exec")
        except SyntaxError:
            continue

        fix = "\n".join(patched_lines[e.lineno - 1 : end_line])
        span = "that line" if end_line == e.lineno else f"lines {e.lineno}-{end_line}"
        reason = (
            f"'{header_line.strip()}' on line {header_lineno} needs its body "
            f"indented one level deeper (column {header_indent + 4}), but line "
            f"{e.lineno} sits at column {first_indent} -- shifting {span} right "
            f"by {offset} space(s) is the largest change the parser confirms "
            f"actually fixes the file, without pulling in a later sibling "
            f"definition."
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

    # Same boundary refinement as _missing_indent_fix(): stop before a
    # span-boundary line (new sibling def/class/decorator, or a bare
    # return/break/continue/raise) at the offending line's own indent,
    # rather than swallowing it into the reindented span.
    max_end_line = e.lineno
    for idx in range(e.lineno, min(len(lines), e.lineno + _INDENT_SEARCH_WINDOW)):
        candidate = lines[idx]
        if candidate.strip():
            candidate_indent = len(candidate) - len(candidate.lstrip())
            if candidate_indent == first_indent and _looks_like_span_boundary(candidate):
                break
        max_end_line = idx + 1

    for target in candidates:
        offset = target - first_indent
        # Prefer the LARGEST span that parses, not the smallest -- same
        # reasoning as _missing_indent_fix(): every line through
        # max_end_line was written at (or nested under) the same
        # original indentation, so they were most likely meant to move
        # together. A larger span underflowing (a deeper-nested line's
        # new indent going negative) doesn't rule out a smaller one
        # working, so that skips to the next size down instead of
        # giving up on this target level entirely.
        for end_line in range(max_end_line, e.lineno - 1, -1):
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
                continue
            try:
                compile("\n".join(patched_lines), "<string>", "exec")
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


def _misaligned_indent_backward_fix(code: str, e: SyntaxError) -> tuple[str, int, int, str]:
    """Covers the case _misaligned_indent_fix() can't: where the
    SyntaxError line itself is fine, and an earlier sibling statement
    immediately above it was the one over-indented, making the offending
    line's dedent look invalid relative to a block level that was never
    supposed to exist. Confirmed live: `total = 0` written one level too
    deep, followed by a correctly-indented `for` loop -- CPython reports
    the error on the `for` line (where the dedent doesn't match), but the
    actual fix is reindenting `total = 0`, not the `for` line or anything
    after it.

    Only handles the "prior line is indented DEEPER than the error line"
    direction -- that's the shape this error message actually produces
    when the true culprit sits above, and it's the one confirmed against
    a real case. Walks upward collecting a contiguous run of lines at
    that same (too-deep) indentation, on the theory they're one statement
    group written together, and reindents the whole run down to the
    error line's own column.

    Returns (fix, start_line, end_line, reason) -- unlike every other fix
    here, start_line is NOT e.lineno, since the lines being replaced sit
    before the reported error."""
    if not e.lineno or not e.msg or e.msg not in _MISALIGNED_INDENT_MESSAGES:
        return "", 0, 0, ""
    lines = code.splitlines()
    if not (0 < e.lineno <= len(lines)):
        return "", 0, 0, ""
    error_line = lines[e.lineno - 1]
    if not error_line.strip():
        return "", 0, 0, ""
    first_indent = len(error_line) - len(error_line.lstrip())

    start_idx = e.lineno - 2  # 0-indexed line immediately above the error
    while start_idx >= 0 and not lines[start_idx].strip():
        start_idx -= 1  # skip blank lines -- they carry no indentation fact
    if start_idx < 0:
        return "", 0, 0, ""
    block_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    if block_indent <= first_indent:
        return "", 0, 0, ""

    begin_idx = start_idx
    while begin_idx > 0:
        prev = lines[begin_idx - 1]
        if not prev.strip() or len(prev) - len(prev.lstrip()) != block_indent:
            break
        begin_idx -= 1

    offset = first_indent - block_indent
    patched_lines = list(lines)
    for idx in range(begin_idx, start_idx + 1):
        line = patched_lines[idx]
        new_indent = len(line) - len(line.lstrip()) + offset
        if new_indent < 0:
            return "", 0, 0, ""
        patched_lines[idx] = " " * new_indent + line.lstrip()

    try:
        compile("\n".join(patched_lines), "<string>", "exec")
    except SyntaxError:
        return "", 0, 0, ""

    fix = "\n".join(patched_lines[begin_idx : start_idx + 1])
    span = "that line" if begin_idx == start_idx else f"lines {begin_idx + 1}-{start_idx + 1}"
    reason = (
        f"line {e.lineno} itself is fine -- {span} right above it sits at "
        f"column {block_indent}, {abs(offset)} space(s) deeper than line "
        f"{e.lineno}'s column {first_indent}; that's what actually makes the "
        f"dedent invalid, so reindenting {span} to column {first_indent} "
        f"(matching line {e.lineno}) is what makes the file parse again."
    )
    return fix, begin_idx + 1, start_idx + 1, reason


def _syntax_error_fix(code: str, e: SyntaxError) -> tuple[str, int, int, ConfidenceTier, str]:
    """Tries each narrow, purpose-built SyntaxError fix in turn and
    returns (fix, start_line, end_line, confidence, reason). The colon
    fix is checked first because it's the only shape unambiguous enough
    to safely chain the iterative collector below past it."""
    colon_fix = _missing_colon_fix(code, e)
    if colon_fix:
        return colon_fix, e.lineno or 0, 0, ConfidenceTier.HIGH, ""

    indent_fix, indent_end_line, indent_reason = _missing_indent_fix(code, e)
    if indent_fix:
        return indent_fix, e.lineno or 0, indent_end_line, ConfidenceTier.MEDIUM, indent_reason

    misaligned_fix, misaligned_end_line, misaligned_reason = _misaligned_indent_fix(code, e)
    if misaligned_fix:
        return (
            misaligned_fix,
            e.lineno or 0,
            misaligned_end_line,
            ConfidenceTier.MEDIUM,
            misaligned_reason,
        )

    backward_fix, backward_start, backward_end, backward_reason = (
        _misaligned_indent_backward_fix(code, e)
    )
    if backward_fix:
        return backward_fix, backward_start, backward_end, ConfidenceTier.MEDIUM, backward_reason

    return "", e.lineno or 0, 0, ConfidenceTier.MEDIUM, ""


_MAX_SYNTAX_ERRORS_PER_REVIEW = 25  # safety cap, not a realistic file's actual count


def _collect_syntax_error_findings(code: str, filename: str) -> list[Finding]:
    findings: list[Finding] = []
    working_lines = code.splitlines()

    for _ in range(_MAX_SYNTAX_ERRORS_PER_REVIEW):
        working_code = "\n".join(working_lines)
        try:
            compile(working_code, filename, "exec")
            break  # every error found so far was auto-fixed; file now parses
        except SyntaxError as e:
            fix, start_line, end_line, confidence, reason = _syntax_error_fix(working_code, e)
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
                    line=start_line or e.lineno or 1,
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
        compile(code, filename, "exec")
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
