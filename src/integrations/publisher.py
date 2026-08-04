"""
src/integrations/publisher.py

Posts a ReviewResult to GitHub: an upserted issue comment (idempotent —
re-running the same commit updates the existing comment rather than
posting a duplicate) plus a Check Run whose conclusion honestly reflects
the PR-gate decision (src/core/pr_gate.py) — never "success" for an
incomplete review. This is the last link in the chain: webhook (Stage 4)
-> Celery task -> orchestrator (Stage 3) -> here.
Every finding with a fix gets that fix, plus the reason it still needs a
human to look at it (src/core/confidence.py's review_reason()), printed
immediately below its line in the comment body -- not just buried in a
separate inline review comment on the Files Changed tab (see
src/cli/review_pr.py's post_fix_suggestions(), which is the other place
this same fix/reason pairing shows up, anchored to the diff line itself
rather than embedded in prose here). Someone reading only the PR's
Conversation tab should be able to see "what's wrong, what to change it
to, and why I still have to do it myself" without switching tabs.
"""
from __future__ import annotations

from src.core.confidence import review_reason
from src.core.models import Finding, ReviewResult
from src.core.pr_gate import GateDecision, decide, gate_reason
from src.integrations.github_client import GitHubClient
from src.storage.comment_store import CommentStore

_DECISION_ICON = {
    GateDecision.APPROVE: "✅",
    GateDecision.BLOCK: "\U0001f534",
    GateDecision.REVIEW_REQUIRED: "⚠️",
}


def publish_review(
    result: ReviewResult,
    pr_number: int,
    *,
    github_client: GitHubClient | None = None,
    comment_store: CommentStore | None = None,
    auto_apply: bool = False,
) -> dict:
    client = github_client or GitHubClient()
    store = comment_store or CommentStore()

    decision = decide(result)
    reason = gate_reason(result)
    body = _build_comment_body(result, decision, reason, auto_apply=auto_apply)

    existing_comment_id = store.get_comment_id(result.repo, result.commit_sha)
    if existing_comment_id is not None:
        client.update_issue_comment(result.repo, existing_comment_id, body)
        comment_id = existing_comment_id
        action = "updated"
    else:
        posted = client.post_issue_comment(result.repo, pr_number, body)
        comment_id = posted["id"]
        store.set_comment_id(result.repo, result.commit_sha, comment_id)
        action = "created"

    check_run = client.create_check_run(
        result.repo, result.commit_sha, decision=decision, summary=reason
    )

    return {
        "comment_action": action,
        "comment_id": comment_id,
        "check_run_id": check_run.get("id"),
        "gate_decision": decision.value,
    }


def _build_comment_body(
    result: ReviewResult, decision: GateDecision, reason: str, *, auto_apply: bool = False
) -> str:
    icon = _DECISION_ICON[decision]
    lines = [
        f"## {icon} AI Code Review — {decision.value.upper()}",
        "",
        f"**Status:** {result.status.value}",
        f"**Findings:** {len(result.findings)} ({result.critical_count} critical)",
        f"**Reason:** {reason}",
        "",
    ]
    if result.findings:
        lines.append("### Findings")
        for finding in result.findings:
            lines.extend(_finding_block(finding, auto_apply=auto_apply))
    return "\n".join(lines)


def _finding_block(finding: Finding, *, auto_apply: bool) -> list[str]:
    end = finding.end_line if finding.end_line >= finding.line else finding.line
    loc = f"{finding.line}" if end == finding.line else f"{finding.line}-{end}"
    block = [f"- `{finding.file}:{loc}` [{finding.severity.value}] {finding.message}"]
    if finding.fix.strip():
        block.append("  Suggested fix:")
        block.append("  ```python")
        block.extend(f"  {fix_line}" for fix_line in finding.fix.splitlines())
        block.append("  ```")
    block.append(f"  _Why this is still here:_ {review_reason(finding, auto_apply=auto_apply)}")
    return block
