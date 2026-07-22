"""
agents/pr_gate.py

PR Gate — blocks or unblocks PR merges via GitHub commit status API.

Flow:
  Critical finding found  → set commit status = FAILED  → PR cannot merge
  All issues resolved     → set commit status = SUCCESS → PR can merge
  Re-review after fix     → compare old vs new findings → post resolution comment

GitHub Status API:
  POST /repos/{owner}/{repo}/statuses/{sha}
  State: "pending" | "success" | "failure" | "error"
"""
from __future__ import annotations

import json
import re
import requests
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

REPORTS_DIR = Path("./reports")


@dataclass
class GateResult:
    blocked:        bool
    reason:         str
    resolved_issues: list[str]   # messages of fixed issues
    new_issues:      list[str]   # messages of newly introduced issues
    still_present:   list[str]   # messages of unfixed issues
    score_before:    float
    score_after:     float


class PRGate:
    """
    Controls whether a PR can be merged.

    Sets GitHub commit status (the green/red checkmark on a PR)
    based on review findings.
    """

    STATUS_CONTEXT = "ai-code-review/security"   # shows up as a check on the PR

    def __init__(self, loader=None):
        # loader is a GitHubLoader instance for auth
        self._loader = loader

    # ─────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────

    def evaluate(
        self,
        repo:       str,
        pr_number:  int,
        head_sha:   str,
        report:     dict,
    ) -> GateResult:
        """
        Main entry point after a review completes.

        1. Load previous review for this PR (if any)
        2. Compare findings: resolved? new? still present?
        3. Block or unblock the PR
        4. Post a status comment
        5. Return GateResult for dashboard
        """
        findings      = report.get("findings", [])
        overall_score = report.get("overall_score", 0.0)
        critical_count = sum(1 for f in findings if f.get("severity") == "critical")

        # Load previous review
        prev = self._load_previous_review(repo, pr_number)

        if prev:
            gate_result = self._compare_and_decide(prev, report)
        else:
            # First review — block if critical issues exist
            gate_result = GateResult(
                blocked         = critical_count > 0,
                reason          = (
                    f"{critical_count} critical issue(s) found — must fix before merge"
                    if critical_count > 0
                    else "No critical issues — PR approved"
                ),
                resolved_issues = [],
                new_issues      = [
                    f.get("message", "")[:100]
                    for f in findings
                    if f.get("severity") == "critical"
                ],
                still_present   = [],
                score_before    = 0.0,
                score_after     = overall_score,
            )

        # Set GitHub commit status
        self._set_commit_status(
            repo     = repo,
            sha      = head_sha,
            blocked  = gate_result.blocked,
            reason   = gate_result.reason,
        )

        # Post resolution/block comment to PR
        self._post_gate_comment(
            repo       = repo,
            pr_number  = pr_number,
            result     = gate_result,
        )

        return gate_result

    # ─────────────────────────────────────────────────────
    # COMPARISON
    # ─────────────────────────────────────────────────────

    def _compare_and_decide(self, prev: dict, current: dict) -> GateResult:
        """
        Compare previous and current review findings.
        A finding is "resolved" if it no longer appears in current.
        A finding is "new" if it only appears in current.
        """
        def fingerprint(f: dict) -> str:
            return (
                f"{f.get('file','')}:"
                f"{f.get('category','')}:"
                f"{(f.get('message') or '')[:80]}"
            )

        prev_fps    = {fingerprint(f): f for f in prev.get("findings", [])}
        curr_fps    = {fingerprint(f): f for f in current.get("findings", [])}

        # Only care about critical/warning for gate decisions
        prev_critical = {
            fp: f for fp, f in prev_fps.items()
            if f.get("severity") in ("critical", "warning")
        }
        curr_critical = {
            fp: f for fp, f in curr_fps.items()
            if f.get("severity") in ("critical", "warning")
        }

        resolved     = [f for fp, f in prev_critical.items() if fp not in curr_fps]
        new_issues   = [f for fp, f in curr_critical.items() if fp not in prev_fps]
        still_present = [f for fp, f in prev_critical.items() if fp in curr_fps]

        score_before = float(prev.get("overall_score", 0.0))
        score_after  = float(current.get("overall_score", 0.0))

        # Block if ANY critical issues still present or newly introduced
        curr_critical_findings = [
            f for f in current.get("findings", [])
            if f.get("severity") == "critical"
        ]
        blocked = len(curr_critical_findings) > 0

        if blocked:
            if still_present and new_issues:
                reason = (
                    f"{len(still_present)} issue(s) still present, "
                    f"{len(new_issues)} new issue(s) introduced"
                )
            elif still_present:
                reason = f"{len(still_present)} critical issue(s) still not fixed"
            else:
                reason = f"{len(new_issues)} new critical issue(s) introduced"
        else:
            reason = (
                f"All {len(resolved)} issue(s) resolved — PR approved! 🎉"
                if resolved
                else "No critical issues — PR approved"
            )

        return GateResult(
            blocked          = blocked,
            reason           = reason,
            resolved_issues  = [f.get("message","")[:100] for f in resolved],
            new_issues       = [f.get("message","")[:100] for f in new_issues],
            still_present    = [f.get("message","")[:100] for f in still_present],
            score_before     = score_before,
            score_after      = score_after,
        )

    # ─────────────────────────────────────────────────────
    # GITHUB STATUS API
    # ─────────────────────────────────────────────────────

    def _set_commit_status(
        self,
        repo:    str,
        sha:     str,
        blocked: bool,
        reason:  str,
    ) -> None:
        """
        Sets the green ✅ / red ❌ commit status that controls
        whether the "Merge" button is enabled on GitHub.

        Requires: Settings → Branches → Add rule → Require status checks
        and add "ai-code-review/security" as a required check.
        """
        if not self._loader:
            print("[gate] No GitHub loader — skipping commit status")
            return

        state       = "failure" if blocked else "success"
        description = reason[:139]   # GitHub limit

        payload = {
            "state":       state,
            "description": description,
            "context":     self.STATUS_CONTEXT,
            "target_url":  f"https://github.com/{repo}/pull/",
        }

        try:
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/statuses/{sha}",
                headers = self._loader.auth.headers(),
                json    = payload,
                timeout = 15,
            )
            resp.raise_for_status()
            icon = "🔴 BLOCKED" if blocked else "✅ UNBLOCKED"
            print(f"[gate] {icon} — commit status set: {description}")
        except Exception as e:
            print(f"[gate] Failed to set commit status: {e}")

    # ─────────────────────────────────────────────────────
    # PR COMMENT
    # ─────────────────────────────────────────────────────

    def _post_gate_comment(
        self,
        repo:      str,
        pr_number: int,
        result:    GateResult,
    ) -> None:
        """Post a summary comment showing what was resolved vs still broken."""
        if not self._loader:
            return

        delta = result.score_after - result.score_before
        delta_str = f"+{delta:.2f}" if delta >= 0 else f"{delta:.2f}"

        lines = []

        if result.blocked:
            lines.append("## 🔴 PR Gate — Changes Required\n")
            lines.append(f"**{result.reason}**\n")
        else:
            lines.append("## ✅ PR Gate — Approved!\n")
            lines.append(f"**{result.reason}**\n")

        lines.append(
            f"**Score:** {result.score_before:.2f} → {result.score_after:.2f} "
            f"({delta_str})\n"
        )

        if result.resolved_issues:
            lines.append(f"\n### ✅ Resolved ({len(result.resolved_issues)} issues fixed)")
            for msg in result.resolved_issues[:5]:
                lines.append(f"- ~~{msg}~~")

        if result.still_present:
            lines.append(f"\n### ❌ Still Present ({len(result.still_present)} not fixed)")
            for msg in result.still_present[:5]:
                lines.append(f"- {msg}")

        if result.new_issues:
            lines.append(f"\n### 🆕 New Issues ({len(result.new_issues)} introduced)")
            for msg in result.new_issues[:5]:
                lines.append(f"- {msg}")

        if result.blocked:
            lines.append(
                "\n---\n*Fix the issues above and push a new commit to unblock this PR.*"
            )
        else:
            lines.append(
                "\n---\n*All critical issues resolved. This PR is cleared for merge.* 🎉"
            )

        body = "\n".join(lines)

        try:
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers = self._loader.auth.headers(),
                json    = {"body": body},
                timeout = 15,
            )
            resp.raise_for_status()
            print(f"[gate] Gate comment posted to PR #{pr_number}")
        except Exception as e:
            print(f"[gate] Failed to post gate comment: {e}")

    # ─────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────

    def _load_previous_review(self, repo: str, pr_number: int) -> dict | None:
        """Load the most recent review report for this PR."""
        pattern = f"review_{repo.replace('/', '_')}_pr{pr_number}_*.json"
        files   = sorted(REPORTS_DIR.glob(pattern), reverse=True)

        if len(files) < 2:
            # Need at least 2 reviews to compare
            return None

        # Return the second-most-recent (the one before the current run)
        try:
            return json.loads(files[1].read_text(encoding="utf-8"))
        except Exception:
            return None