"""
memory/review_memory.py

Feature 8: LangGraph Memory
Remembers patterns across PRs per developer.
Personalizes review messages for repeat offenders.

Stored in: vectordb/memory.json
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict


MEMORY_PATH = Path("./vectordb/memory.json")


class ReviewMemory:

    def __init__(self):
        self._data = self._load()

    # ── Public API ────────────────────────────────────────

    def record_review(
        self,
        author: str,
        repo: str,
        pr_number: int,
        findings: list[dict],
    ) -> None:
        """Store findings per author after each review."""
        if author not in self._data:
            self._data[author] = {
                "total_prs":     0,
                "total_critical": 0,
                "patterns":      {},  # category → count
                "recent_issues": [],
                "pr_history":    [],
            }

        record = self._data[author]
        record["total_prs"] += 1

        critical_this_pr = 0
        for f in findings:
            cat = f.get("category", "style")
            record["patterns"][cat] = record["patterns"].get(cat, 0) + 1
            if f.get("severity") == "critical":
                critical_this_pr += 1
                record["total_critical"] += 1
                # Keep last 10 critical issues
                record["recent_issues"].append({
                    "file":     f.get("file", ""),
                    "message":  f.get("message", "")[:120],
                    "category": cat,
                    "pr":       pr_number,
                    "date":     datetime.utcnow().isoformat(),
                })
                record["recent_issues"] = record["recent_issues"][-10:]

        record["pr_history"].append({
            "repo":      repo,
            "pr":        pr_number,
            "critical":  critical_this_pr,
            "date":      datetime.utcnow().isoformat(),
        })
        record["pr_history"] = record["pr_history"][-20:]

        self._save()
        print(f"[memory] Recorded review for author: {author}")

    def get_context(self, author: str) -> str:
        """
        Returns a personalized context string to prepend to the LLM prompt.
        If the author has a history of repeated mistakes, the LLM is told
        to pay extra attention to those patterns.
        """
        if author not in self._data:
            return ""

        record = self._data[author]
        total  = record["total_prs"]

        if total < 2:
            return ""

        # Find top repeated patterns
        patterns = record["patterns"]
        top = sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:3]

        lines = [
            f"\n=== DEVELOPER HISTORY ({author}) ===",
            f"PRs reviewed: {total}",
            f"Total critical issues: {record['total_critical']}",
        ]

        if top:
            lines.append(
                "Most frequent issue categories: "
                + ", ".join(f"{cat} ({n}x)" for cat, n in top)
            )

        # Warn about repeated security issues
        security_count = patterns.get("security", 0)
        if security_count >= 3:
            lines.append(
                f"⚠️  This developer has had {security_count} security issues "
                f"in past PRs. Pay EXTRA attention to security vulnerabilities."
            )

        # Recent critical issues
        recent = record["recent_issues"][-3:]
        if recent:
            lines.append("Recent critical issues from this developer:")
            for issue in recent:
                lines.append(
                    f"  - [{issue['category']}] {issue['message'][:80]} (PR #{issue['pr']})"
                )

        lines.append("=== END DEVELOPER HISTORY ===\n")
        return "\n".join(lines)

    def get_stats(self, author: str) -> dict:
        """Return raw stats for a developer."""
        return self._data.get(author, {})

    def get_all_authors(self) -> list[str]:
        return list(self._data.keys())

    def mark_issue_resolved(
        self,
        author: str,
        category: str,
        pr_number: int,
    ) -> None:
        """Call this when an issue is confirmed fixed in a follow-up PR."""
        if author not in self._data:
            return
        # Reduce the pattern count when resolved
        patterns = self._data[author]["patterns"]
        if category in patterns and patterns[category] > 0:
            patterns[category] -= 1
        self._save()

    # ── Storage ───────────────────────────────────────────

    def _load(self) -> dict:
        if MEMORY_PATH.exists():
            try:
                return json.loads(MEMORY_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _save(self) -> None:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(json.dumps(self._data, indent=2))
        