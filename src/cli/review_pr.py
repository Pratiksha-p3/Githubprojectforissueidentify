"""
src/cli/review_pr.py

`review-cli review-pr <repo> <pr_number>` — points the reviewer at a real,
already-open GitHub PR instead of a locally-supplied file: fetches every
changed `.py` file at the PR's head commit (src/integrations/
github_client.py's list_pr_files()/get_file_content()), runs the same
orchestrator every other entry point uses, and prints the result.

Dry-run by default (`--post` is required to actually publish) — this
posts a real, visible comment + Check Run onto someone's actual PR, which
is a different category of action from every other command in this CLI
(analyze/review/index only ever touch local files or a local vector
store). Requiring an explicit flag means a first-time or scripted run
can't silently spam a PR without the caller having deliberately opted in.

All the PR's changed files are combined into ONE ReviewResult (repo +
the PR's head sha) before publishing, not one per file — publish_review()
upserts a single comment keyed on (repo, commit_sha), so calling it once
per file against the same commit would just overwrite the same comment
repeatedly, leaving only the last file's findings visible instead of a
combined summary of the whole PR.

`--post` also posts one inline review comment per finding that has a
non-empty `fix`, using GitHub's ```suggestion fenced-block syntax
(src/integrations/github_client.py's create_review_comment()) — this is
what turns a Finding's fix from prose in a summary comment into an
actual one-click "Apply suggestion" button on the PR's Files Changed
tab. Each such comment also includes its confidence tier and
src/core/confidence.py's manual_review_reason() text, so "why does this
need a human to click Apply rather than happening on its own" is visible
right on GitHub, not just in this CLI's own output. Findings with no fix
(e.g. a syntax error) are skipped since there's nothing to suggest.

Two different counts get reported, deliberately not conflated:
"Auto-fixed" (src/core/confidence.py's is_safe_to_auto_apply() gate --
currently always 0, since no checker or LLM finding source in this
project produces HIGH confidence) versus "Fix suggestions posted to
GitHub" (how many inline suggestion comments this run actually
published -- each still needs a human to click "Apply suggestion").
The second number can be non-zero; conflating it with the first would
misrepresent something a human still has to act on as something the
system did on its own.

`--multi-agent` swaps the single runtime/logic LLM pass for
src/agents/coordinator.py's four specialized agents (adds security,
style, test_coverage) — off by default, same reasoning as
src/core/orchestrator.py's own use_multi_agent default (multiplies LLM
calls per file 4x). Findings like a hardcoded secret or a SQL-injection
shaped query that the security_agent would catch never surface here
without it; the deterministic checkers (src/analyzers/
hardcoded_secret_checker.py, sql_injection_checker.py) catch the most
common instances of both without needing this flag at all.
"""
from __future__ import annotations

import argparse
import sys

import requests

from src.core.confidence import manual_review_reason, summarize_auto_fix_status
from src.core.models import Finding, ReviewResult, ReviewStatus
from src.core.orchestrator import review_code
from src.core.pr_gate import GateDecision, decide, gate_reason
from src.integrations.github_client import GitHubClient
from src.integrations.publisher import publish_review

_DECISION_ICON = {
    GateDecision.APPROVE: "✅",
    GateDecision.BLOCK: "\U0001f534",
    GateDecision.REVIEW_REQUIRED: "⚠️",
}

# Worst-first: one FAILED file makes the whole PR FAILED, one DEGRADED
# (with no FAILED) makes it DEGRADED, otherwise COMPLETED -- the same
# "never let a partial failure look like a clean pass" principle
# src/core/orchestrator.py already applies within a single file.
_STATUS_SEVERITY = {ReviewStatus.FAILED: 2, ReviewStatus.DEGRADED: 1, ReviewStatus.COMPLETED: 0}


def review_pr(
    repo: str,
    pr_number: int,
    *,
    include_llm: bool = True,
    use_multi_agent: bool = False,
    post: bool = False,
    github_client: GitHubClient | None = None,
) -> int:
    client = github_client or GitHubClient()

    pr = client.get_pull_request(repo, pr_number)
    head_sha = pr["head"]["sha"]

    files = client.list_pr_files(repo, pr_number)
    py_files = [f for f in files if f["filename"].endswith(".py") and f["status"] != "removed"]

    if not py_files:
        print(f"No changed .py files in {repo}#{pr_number} — nothing to review.")
        return 0

    print(f"Reviewing {len(py_files)} file(s) from {repo}#{pr_number} @ {head_sha[:7]}...\n")

    results: list[ReviewResult] = []
    for f in py_files:
        path = f["filename"]
        code = client.get_file_content(repo, path, ref=head_sha)
        result = review_code(
            code, path, repo=repo, commit_sha=head_sha,
            include_llm=include_llm, use_multi_agent=use_multi_agent,
        )
        results.append(result)
        _print_file_result(path, result)

    combined = _combine(repo, head_sha, results)
    decision = decide(combined)
    reason = gate_reason(combined)

    print(f"\n{'=' * 60}")
    print(f"  Overall: {repo}#{pr_number}")
    print(f"{'=' * 60}")
    print(f"  Status:   {combined.status.value}")
    print(f"  Findings: {len(combined.findings)} ({combined.critical_count} critical)")
    print(f"  Decision: {_DECISION_ICON[decision]} {decision.value.upper()}")
    print(f"  Reason:   {reason}")
    print(f"{'=' * 60}")

    fix_status = summarize_auto_fix_status(combined.findings)
    print(f"  Auto-fixed:              {fix_status['auto_fixed_count']}")
    print(f"  Needs manual review:     {fix_status['manual_review_count']}")
    for detail in fix_status["manual_review_details"]:
        print(f"    - {detail['file']}:{detail['line']} ({detail['source']}) — {detail['reason']}")
    print(f"{'=' * 60}")

    if post:
        try:
            outcome = publish_review(combined, pr_number, github_client=client)
            print(
                f"\nPosted to GitHub: comment {outcome['comment_action']} "
                f"(id={outcome['comment_id']}), check run id={outcome['check_run_id']}"
            )
        except requests.exceptions.HTTPError as e:
            # Check Run creation requires GitHub App auth -- a PAT (this
            # client's only supported auth mode, see github_client.py's
            # docstring) gets a 403 here even with full repo permissions.
            # The summary comment itself already succeeded (publish_review
            # posts it before attempting the check run), so this is a
            # disclosed, known gap, not a reason to also skip the fix
            # suggestions below, which don't depend on the check run at all.
            print(f"\nComment posted, but check run creation failed: {e}")
            print("(Check Runs need GitHub App auth, not a personal access token.)")

        suggestion_count = post_fix_suggestions(
            client, repo, pr_number, head_sha, combined.findings
        )
        print(f"{'=' * 60}")
        print(f"  Fix suggestions posted to GitHub: {suggestion_count}")
        print("  (each still needs a human to click \"Apply suggestion\" -- see")
        print("   the reason posted alongside each one on the PR itself)")
        print(f"{'=' * 60}")
    else:
        print("\n(dry run -- nothing posted to GitHub; re-run with --post to publish)")

    return 0 if decision != GateDecision.BLOCK else 1


def post_fix_suggestions(
    client: GitHubClient, repo: str, pr_number: int, commit_sha: str, findings: list[Finding]
) -> int:
    posted = 0
    for f in findings:
        if not f.fix.strip():
            continue
        reason = manual_review_reason(f) or "High confidence — safe to auto-apply."
        body = (
            f"**[{f.severity.value.upper()}] {f.message}**\n\n"
            f"```suggestion\n{f.fix}\n```\n\n"
            f"*Why this needs a human to click \"Apply suggestion\" rather than "
            f"happening on its own ({f.confidence.value} confidence):* {reason}"
        )
        client.create_review_comment(
            repo, pr_number, commit_id=commit_sha, path=f.file, line=f.line, body=body
        )
        posted += 1
    return posted


def _print_file_result(path: str, result: ReviewResult) -> None:
    print(f"  {path} — {result.status.value}, {len(result.findings)} finding(s)")
    for f in result.findings:
        print(f"    [{f.severity.value.upper()}] Line {f.line} — {f.message} (source: {f.source})")
        if f.fix:
            print(f"      Suggested fix ({f.confidence.value} confidence):")
            for line in f.fix.splitlines():
                print(f"        {line}")
        else:
            print("      (no auto-generated fix for this finding)")


def _combine(repo: str, commit_sha: str, results: list[ReviewResult]) -> ReviewResult:
    worst_status = max((r.status for r in results), key=lambda s: _STATUS_SEVERITY[s])
    all_findings = [f for r in results for f in r.findings]
    return ReviewResult(
        repo=repo,
        commit_sha=commit_sha,
        status=worst_status,
        findings=all_findings,
        summary=f"{len(results)} file(s) reviewed, {len(all_findings)} finding(s) total.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review-cli review-pr")
    parser.add_argument("repo", help="owner/name, e.g. acme/widgets")
    parser.add_argument("pr_number", type=int, help="Pull request number")
    parser.add_argument(
        "--no-llm", action="store_false", dest="include_llm",
        help="Skip the LLM supplement pass (deterministic checkers only)",
    )
    parser.add_argument(
        "--multi-agent", action="store_true", dest="use_multi_agent",
        help="Use the 4 specialized agents (security/style/test_coverage/logic) "
        "instead of the single runtime/logic pass",
    )
    parser.add_argument(
        "--post", action="store_true",
        help="Actually post the review comment + check run to GitHub (default: dry run)",
    )
    args = parser.parse_args(argv)
    return review_pr(
        args.repo, args.pr_number,
        include_llm=args.include_llm, use_multi_agent=args.use_multi_agent, post=args.post,
    )


if __name__ == "__main__":
    sys.exit(main())
