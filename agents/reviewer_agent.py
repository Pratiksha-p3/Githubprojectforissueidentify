"""
agents/reviewer_agent.py
AI Code Reviewer using Groq (free) LLM.
"""
from __future__ import annotations

import json
from typing import Any


from config import cfg

try:
    from prompt.prompt import SYSTEM_PROMPT, build_prompt, build_summary_prompt
except ImportError:  # pragma: no cover - fallback for package-relative execution
    from ..prompt.prompt import SYSTEM_PROMPT, build_prompt, build_summary_prompt

from rag.retriever import Retriever
from ingestion.github_loader import PRFile
from agents.security_agent import SecurityAgent, merge_findings

_REQUIRED_KEYS  = {"findings", "summary", "overall_score", "test_coverage_gaps"}
_VALID_SEVERITY = {"critical", "warning", "info"}


class ReviewerAgent:

    def __init__(self, retriever: Retriever = None):
        self.retriever    = retriever or Retriever()
        self.security     = SecurityAgent()
        self._retry_limit = 2
        self._groq_client = None

    # ── Public ────────────────────────────────────────────

    def review_file(
        self,
        pr_file: PRFile,
        pr_title: str = "",
        pr_description: str = "",
    ) -> dict[str, Any]:

        context_chunks = self.retriever.retrieve_for_file(pr_file)

        user_prompt = build_prompt(
            pr_file=pr_file,
            context_chunks=context_chunks,
            pr_title=pr_title,
            pr_description=pr_description,
        )

        result = self._call_llm_with_retry(user_prompt)
        result = self._validate_and_clean(result, pr_file.filename)

        # Run deterministic security scan and merge with LLM findings
        semgrep_findings = self.security.scan_file(pr_file)
        print(f"[DEBUG] Semgrep findings: {len(semgrep_findings)}")

        for f in semgrep_findings:
            print(
        f"[DEBUG] {f['file']}:{f['line']} "
        f"{f['severity']} {f['message']}"
    )
        if semgrep_findings:
            result["findings"] = merge_findings(result["findings"], semgrep_findings)
            # A confirmed static-analysis critical finding should tank the score
            if any(f["severity"] == "critical" for f in semgrep_findings):
                result["overall_score"] = min(result["overall_score"], 0.3)

        return result

    
    def review_pr(
        self,
        files: list[PRFile],
        pr_title: str = "",
        pr_description: str = "",
    ) -> dict:

        all_findings: list[dict] = []
        file_scores:  list[float] = []
        file_reviews: list[dict] = []

        for pf in files:
            review = self.review_file(pf, pr_title, pr_description)
            file_reviews.append({"file": pf.filename, "review": review})
            all_findings.extend(review.get("findings", []))
            file_scores.append(review.get("overall_score", 1.0))

        overall_score = min(file_scores) if file_scores else 1.0
        all_findings  = _deduplicate_findings(all_findings)
        # Skip the extra executive-summary LLM call if any per-file review
        # already failed (likely rate-limited) — saves a guaranteed-to-fail
        # API call and lets you see partial results immediately.
        any_review_failed = any(
            "RATE LIMITED" in fr["review"].get("summary", "")
            or "Review failed" in fr["review"].get("summary", "")
            for fr in file_reviews
        )
        if any_review_failed:
            print("[reviewer] Skipping executive summary (per-file review already failed/rate-limited)")
            executive = {}
        else:
            executive = self._generate_executive_summary(file_reviews, pr_title)
        
        critical_count = sum(
    1 for f in all_findings
    if f.get("severity") == "critical"
)

        warning_count = sum(
    1 for f in all_findings
    if f.get("severity") == "warning"
)

        info_count = sum(
    1 for f in all_findings
    if f.get("severity") == "info"
)

        risk_score = sum(
    f.get(
        "risk_weight",
        50 if f.get("severity") == "critical"
        else 10 if f.get("severity") == "warning"
        else 2
    )
    for f in all_findings
)

        if critical_count > 0:
            risk_level = "HIGH"
        elif risk_score >= 20:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        
        approved = (
    not any_review_failed
    and overall_score >= 0.85
    and critical_count == 0
)

        return {
    "overall_score": round(overall_score, 2),

    "risk_score": risk_score,
    "risk_level": risk_level,

    "total_findings": len(all_findings),

    "critical_count": critical_count,
    "warning_count": warning_count,
    "info_count": info_count,

    "findings": all_findings,
    "files": file_reviews,
    "executive_summary": executive,

    "approved": approved,
    }
        
        

    # ── LLM Dispatch ─────────────────────────────────────

    def _call_llm_with_retry(self, user_prompt: str) -> dict:
        import time
        last_err = None
        for attempt in range(self._retry_limit):
            try:
                raw = self._call_llm(user_prompt)
                if _REQUIRED_KEYS.issubset(raw.keys()):
                    return raw
                print(f"[reviewer] Attempt {attempt+1}: missing keys, retrying…")
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "rate_limit" in err_str or "429" in err_str:
                    print(f"[reviewer] Rate limited (attempt {attempt+1}). "
                          f"Free Groq tier resets daily — see error for wait time.")
                    break  # retrying immediately won't help with a daily limit
                print(f"[reviewer] Attempt {attempt+1} error: {e}")

        is_rate_limit = last_err and ("rate_limit" in str(last_err) or "429" in str(last_err))
        return {
            "findings": [],
            "summary": (
                f"[RATE LIMITED — LLM did not run] {last_err}"
                if is_rate_limit else f"Review failed: {last_err}"
            ),
            "overall_score": 0.5 if is_rate_limit else 0.0,  # neutral, not "code is bad"
            "test_coverage_gaps": [],
        }

    def _call_llm(self, user_prompt: str) -> dict:
        return self._call_groq(user_prompt)

    def _call_groq(self, user_prompt: str) -> dict:
        if self._groq_client is None:
            from groq import Groq
            self._groq_client = Groq(api_key=cfg.groq_api_key)

        response = self._groq_client.chat.completions.create(
            model=cfg.review_model,
            temperature=0,
            max_tokens=cfg.max_review_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        return _safe_json_parse(content)

    # ── Validation ────────────────────────────────────────

    def _validate_and_clean(self, review: dict, filename: str) -> dict:
        review.setdefault("findings", [])
        review.setdefault("summary", "")
        review.setdefault("test_coverage_gaps", [])

        score = float(review.get("overall_score", 1.0))
        review["overall_score"] = max(0.0, min(1.0, score))

        clean = []

        for f in review["findings"]:

            if self._is_false_positive(f):
                print(f"[reviewer] Filtered false positive: {f.get('message','')}")
                continue

            if not isinstance(f, dict):
                continue

            f["severity"] = str(f.get("severity", "info")).lower()
            if f["severity"] not in _VALID_SEVERITY:
                f["severity"] = "info"
            try:
                f["line"] = int(f.get("line", 0))
            except (TypeError, ValueError):
                f["line"] = 0
            if not f.get("message"):
                continue
            f.setdefault("fix", "")
            f.setdefault("category", "style")
            f["file"] = filename
            clean.append(f)

        review["findings"] = clean
        return review
    
    def _is_false_positive(self, finding: dict) -> bool:
        msg = (finding.get("message") or "").lower()
        fix = (finding.get("fix") or "").lower()

        ignore_patterns = [
    "os.getenv(",
    "api_key",
    "db_password",
    "sqlite3.connect",
    "dependency injection",
    "database connection created directly",
    "hardcoded secret",
]

        return any(
    p in msg or p in fix
    for p in ignore_patterns
)


    def _generate_executive_summary(self, file_reviews: list[dict], pr_title: str) -> dict:
        """
        Generate a PR-level roll-up.
        Skips the extra LLM call (saves tokens) when total findings are
        low/simple enough to summarise deterministically instead.
        """
        if not file_reviews:
            return {}

        total_findings = sum(len(fr["review"].get("findings", [])) for fr in file_reviews)
        has_critical = any(
            f.get("severity") == "critical"
            for fr in file_reviews
            for f in fr["review"].get("findings", [])
        )

        # For small reviews, build summary from existing data — no extra API call.
        if total_findings <= 5:
            top_risks = [
                f"{f.get('file','')}:{f.get('line',0)} — {f.get('message','')[:80]}"
                for fr in file_reviews
                for f in fr["review"].get("findings", [])
                if f.get("severity") == "critical"
            ][:3]
            return {
                "executive_summary": (
                    f"Reviewed {len(file_reviews)} file(s), found {total_findings} issue(s). "
                    + ("Critical security issues require immediate attention before merge."
                       if has_critical else "No critical issues found.")
                ),
                "top_risks": top_risks,
                "recommended_actions": ["Fix all critical findings before merging"] if has_critical else [],
                "approve": not has_critical,
            }

        # For larger reviews, the extra LLM call is worth the token cost.
        try:
            prompt = build_summary_prompt(file_reviews, pr_title)
            return self._call_llm(prompt) or {}
        except Exception as e:
            print(f"[reviewer] Summary failed: {e}")
            return {}


# ── Helpers ───────────────────────────────────────────────

def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    # Strip markdown fences if present
    if "```" in text:
        lines = text.splitlines()
        text  = "\n".join(
            l for l in lines
            if not l.strip().startswith("```")
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re

        # Try to find JSON object inside the text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # The response was likely truncated mid-JSON (hit max_tokens).
        # Attempt a repair: close any unterminated strings/arrays/objects.
        repaired = _attempt_json_repair(text)
        if repaired is not None:
            return repaired

        raise ValueError(f"Could not parse JSON from LLM response:\n{text[:300]}")


def _attempt_json_repair(text: str) -> dict | None:
    """
    Best-effort repair for JSON truncated mid-stream (common when the LLM
    hits max_tokens before finishing). Closes unclosed brackets/braces and
    strips a trailing incomplete value. Returns None if repair fails.
    """
    import re

    # Find where the JSON object starts
    start = text.find("{")
    if start == -1:
        return None
    text = text[start:]

    # Cut off any trailing incomplete key/value (e.g. ends mid-string)
    # by trimming back to the last complete comma or closing bracket.
    last_safe = max(text.rfind(","), text.rfind("}"), text.rfind("]"))
    if last_safe > 0:
        candidate = text[: last_safe + 1]
    else:
        candidate = text

    # Count and close any unbalanced braces/brackets
    open_braces   = candidate.count("{") - candidate.count("}")
    open_brackets = candidate.count("[") - candidate.count("]")

    # Remove trailing comma before closing, if present
    candidate = re.sub(r",\s*$", "", candidate)

    candidate += "]" * max(0, open_brackets)
    candidate += "}" * max(0, open_braces)

    try:
        result = json.loads(candidate)
        print("[reviewer] Repaired truncated JSON response (some data may be incomplete)")
        return result
    except json.JSONDecodeError:
        return None


def _deduplicate_findings(findings: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique = []
    for f in findings:
        key = (
            f.get("file", ""),
            f.get("line", 0),
            f.get("category", ""),
            (f.get("message") or "")[:60],
        )
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class DummyPR:
        filename: str
        language: str
        patch: str
        full_content: str
        additions: int = 0
        deletions: int = 0
        changed_lines: list = None

    sample = DummyPR(
        filename="auth.py",
        language="python",
        patch='@@ -0,0 +1,2 @@\n+password = "admin123"\n+print(password)',
        full_content='password = "admin123"\nprint(password)\n',
        changed_lines=['password = "admin123"', "print(password)"],
    )

    agent  = ReviewerAgent()
    result = agent.review_file(sample, pr_title="Add Login Logic")
    print(json.dumps(result, indent=2))