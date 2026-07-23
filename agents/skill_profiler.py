"""
agents/skill_profiler.py

Developer skill-gap profiling.

Every review already produces structured findings (category, severity,
message, file, line). This agent persists those findings per PR author
across reviews (flat JSON files — no new infra needed) and aggregates
them into a profile: which categories of mistakes this developer makes
most often, so recurring gaps (e.g. "keeps writing unparameterized SQL",
"keeps missing None checks") surface instead of getting lost PR-by-PR.

Storage: reports/skill_profiles/<author>.json
  Each file is a capped history of (timestamp, repo, pr_number, category,
  severity, message) entries. Capped at MAX_HISTORY per author so the file
  doesn't grow unbounded on a long-lived repo.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

PROFILE_DIR = Path("reports/skill_profiles")
MAX_HISTORY = 500

# Category -> suggested focus area. Deterministic, not LLM-generated, so
# profiling a developer never costs an extra API call.
_FOCUS_AREAS = {
    "security":     "Review OWASP Top 10 patterns (injection, secrets, weak crypto) before opening PRs.",
    "runtime":      "Add defensive checks for None/empty/out-of-range inputs before using them.",
    "logic":        "Double-check conditionals and return values against the intended behavior with a test case.",
    "syntax":       "Run a linter/formatter locally before pushing to catch these before review.",
    "architecture": "Re-read the relevant ADRs in docs/adr/ before touching this area of the codebase.",
    "compliance":   "Re-read the relevant standards in docs/standards/ before touching this area of the codebase.",
    "style":        "Configure the project's linter in your editor to catch these automatically.",
}


class SkillProfiler:

    def __init__(self, profile_dir: str | Path = PROFILE_DIR):
        self.profile_dir = Path(profile_dir)

    # ── Public API ────────────────────────────────────────

    def record_review(
        self,
        author: str,
        repo: str,
        pr_number: int,
        findings: list[dict],
        reviewed_at: str,
    ) -> None:
        """Append this PR's findings to the author's persisted history."""
        if not author:
            return

        history = self._load_raw(author)
        for f in findings:
            if not isinstance(f, dict):
                continue
            category = f.get("category", "")
            severity = f.get("severity", "")
            if not category:
                continue
            history.append({
                "reviewed_at": reviewed_at,
                "repo": repo,
                "pr_number": pr_number,
                "category": category,
                "severity": severity,
                "message": (f.get("message") or "")[:200],
                "file": f.get("file", ""),
            })

        history = history[-MAX_HISTORY:]
        self._save_raw(author, history)

    def get_profile(self, author: str) -> dict:
        """Aggregate stats from this author's full recorded history."""
        history = self._load_raw(author)
        if not history:
            return {
                "author": author,
                "total_reviews": 0,
                "total_findings": 0,
                "by_category": {},
                "by_severity": {},
                "top_categories": [],
                "recurring_patterns": [],
            }

        pr_keys = {(h["repo"], h["pr_number"]) for h in history}
        by_category = Counter(h["category"] for h in history)
        by_severity = Counter(h["severity"] for h in history if h["severity"])

        return {
            "author": author,
            "total_reviews": len(pr_keys),
            "total_findings": len(history),
            "by_category": dict(by_category),
            "by_severity": dict(by_severity),
            "top_categories": by_category.most_common(5),
            "recurring_patterns": self._recurring_patterns(history),
            "first_seen": history[0]["reviewed_at"],
            "last_seen": history[-1]["reviewed_at"],
        }

    def generate_gap_report(self, author: str) -> dict:
        """Profile + a short, deterministic coaching summary."""
        profile = self.get_profile(author)
        if profile["total_findings"] == 0:
            return {**profile, "gaps": [], "summary": "No findings history yet."}

        gaps = []
        for category, count in profile["top_categories"]:
            if count < 2:
                continue  # a single occurrence isn't a "gap", it's noise
            gaps.append({
                "category": category,
                "occurrences": count,
                "focus_area": _FOCUS_AREAS.get(category, "Review these findings with a teammate."),
            })

        if gaps:
            top = gaps[0]
            summary = (
                f"{author} has {profile['total_findings']} finding(s) across "
                f"{profile['total_reviews']} reviewed PR(s). Most recurring category: "
                f"'{top['category']}' ({top['occurrences']} times) — {top['focus_area']}"
            )
        else:
            summary = (
                f"{author} has {profile['total_findings']} finding(s) across "
                f"{profile['total_reviews']} reviewed PR(s), no strongly recurring category yet."
            )

        return {**profile, "gaps": gaps, "summary": summary}

    # ── Internal ──────────────────────────────────────────

    def _recurring_patterns(self, history: list[dict], top_n: int = 5) -> list[dict]:
        """
        Findings across different PRs often reuse near-identical message
        text (same rule firing repeatedly) — group on a normalized message
        (numbers/quoted values stripped) so "line 12" vs "line 40" still
        count as the same recurring mistake.
        """
        normalized = Counter()
        examples: dict[str, str] = {}
        for h in history:
            key = re.sub(r"['\"`].+?['\"`]", "<value>", h["message"])
            key = re.sub(r"\d+", "<n>", key)
            normalized[key] += 1
            examples.setdefault(key, h["message"])

        return [
            {"pattern": examples[key], "occurrences": count}
            for key, count in normalized.most_common(top_n)
            if count >= 2
        ]

    def _load_raw(self, author: str) -> list[dict]:
        path = self._path_for(author)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_raw(self, author: str, history: list[dict]) -> None:
        path = self._path_for(author)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    def _path_for(self, author: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", author) or "unknown"
        return self.profile_dir / f"{safe}.json"
