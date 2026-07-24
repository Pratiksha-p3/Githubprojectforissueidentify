"""
agents/incremental_agent.py

Feature 5: Incremental Re-Review
When a developer pushes a new commit to fix issues, only re-review
changed files and compare against the previous review.
Posts "✅ Issue resolved" or "❌ Still present" comments.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime


REVIEWS_DIR = Path("./reports")


def fingerprint(f: dict) -> str:
    """
    Identifies a finding across two review runs regardless of exact line
    number (a line shifting by a couple lines shouldn't count as a "new"
    finding) — shared by compare_reviews() and Notifier so both agree on
    what counts as "the same finding".
    """
    return (
        f"{f.get('file', '')}"
        f":{f.get('category', '')}"
        f":{(f.get('message') or '')[:60]}"
    )


class IncrementalAgent:

    def __init__(self):
        self._reviews: dict[str, dict] = {}

    def load_previous_review(self, repo: str, pr_number: int) -> dict | None:
        """Load the most recent review for this PR."""
        pattern = f"review_{repo.replace('/', '_')}_pr{pr_number}_*.json"
        files   = sorted(REVIEWS_DIR.glob(pattern), reverse=True)

        if not files:
            return None

        try:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            print(f"[incremental] Loaded previous review: {files[0].name}")
            return data
        except Exception as e:
            print(f"[incremental] Could not load previous review: {e}")
            return None

    def compare_reviews(
        self,
        previous: dict,
        current: dict,
    ) -> dict:
        """
        Compare previous and current review findings.
        Returns:
          resolved  — issues that were in previous but not in current
          new       — issues that appeared in current but not previous
          persisted — issues that appear in both (not fixed)
        """
        prev_fps = {fingerprint(f): f for f in previous.get("findings", [])}
        curr_fps = {fingerprint(f): f for f in current.get("findings", [])}

        resolved  = [f for fp, f in prev_fps.items() if fp not in curr_fps]
        new       = [f for fp, f in curr_fps.items() if fp not in prev_fps]
        persisted = [f for fp, f in curr_fps.items() if fp in prev_fps]

        comparison = {
            "resolved":         resolved,
            "new_issues":       new,
            "persisted_issues": persisted,
            "prev_score":       previous.get("overall_score", 0),
            "curr_score":       current.get("overall_score", 0),
            "score_delta":      round(
                current.get("overall_score", 0) -
                previous.get("overall_score", 0), 2
            ),
            "improved": (
                current.get("overall_score", 0) >
                previous.get("overall_score", 0)
            ),
        }

        self._print_comparison(comparison)
        return comparison

    def build_followup_comment(self, comparison: dict) -> str:
        """Build a GitHub comment summarising what changed since last review."""
        lines = ["## 🔄 Re-Review — What Changed\n"]

        delta = comparison["score_delta"]
        prev  = comparison["prev_score"]
        curr  = comparison["curr_score"]

        if delta > 0:
            lines.append(f"**Score improved:** {prev:.2f} → {curr:.2f} (+{delta:.2f}) ✅\n")
        elif delta < 0:
            lines.append(f"**Score dropped:** {prev:.2f} → {curr:.2f} ({delta:.2f}) ❌\n")
        else:
            lines.append(f"**Score unchanged:** {curr:.2f}\n")

        # Resolved issues
        if comparison["resolved"]:
            lines.append(f"\n### ✅ Resolved ({len(comparison['resolved'])} issues fixed)")
            for f in comparison["resolved"][:5]:
                lines.append(
                    f"- ~~`{f.get('file','')}:L{f.get('line',0)}` "
                    f"{f.get('message','')[:80]}~~"
                )

        # New issues
        if comparison["new_issues"]:
            lines.append(f"\n### 🆕 New Issues ({len(comparison['new_issues'])} introduced)")
            for f in comparison["new_issues"][:5]:
                icon = "🔴" if f.get("severity") == "critical" else "🟡"
                lines.append(
                    f"- {icon} `{f.get('file','')}:L{f.get('line',0)}` "
                    f"{f.get('message','')[:80]}"
                )

        # Persisted issues
        if comparison["persisted_issues"]:
            lines.append(
                f"\n### ❌ Still Present ({len(comparison['persisted_issues'])} not fixed)"
            )
            for f in comparison["persisted_issues"][:5]:
                lines.append(
                    f"- `{f.get('file','')}:L{f.get('line',0)}` "
                    f"{f.get('message','')[:80]}"
                )

        lines.append(f"\n*Re-reviewed at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*")
        return "\n".join(lines)

    def post_comparison_comment(
        self,
        repo: str,
        pr_number: int,
        comparison: dict,
    ) -> None:
        """Post the comparison summary as a PR comment."""
        try:
            from ingestion.github_loader import GitHubLoader
            loader  = GitHubLoader()
            comment = self.build_followup_comment(comparison)

            import requests
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers=loader.auth.headers(),
                json={"body": comment},
                timeout=15,
            )
            resp.raise_for_status()
            print(f"[incremental] Comparison comment posted to PR #{pr_number}")
        except Exception as e:
            print(f"[incremental] Could not post comparison comment: {e}")

    def _print_comparison(self, c: dict) -> None:
        resolved  = len(c["resolved"])
        new       = len(c["new_issues"])
        persisted = len(c["persisted_issues"])
        delta     = c["score_delta"]

        print(f"\n[incremental] Comparison:")
        print(f"  ✅ Resolved  : {resolved}")
        print(f"  🆕 New       : {new}")
        print(f"  ❌ Persisted : {persisted}")
        print(f"  📈 Score Δ  : {delta:+.2f}")