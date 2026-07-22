"""
agents/advanced_langgraph.py

Advanced LangGraph Pipeline — All 8 Features

6-node graph:

  ┌─────────────┐
  │   PLANNER   │  1. Analyse files, build review plan
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  EXECUTOR   │  2. RAG + LLM + Semgrep per file
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  CVE LOOKUP │  3. Enrich security findings with CVE data
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  AUTO-FIX   │  1. Generate actual fixed code
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  PUBLISHER  │  4. PR approval gate, inline comments, PR block
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  NOTIFIER   │  7. Slack + email on critical findings
  └─────────────┘

Memory (Feature 8) is read at planner and written at publisher.
Incremental re-review (Feature 5) is checked at planner.
Dashboard (Feature 6) reads from saved reports automatically.

Usage:
  from agents.advanced_langgraph import run_advanced_review

  result = run_advanced_review(
      pr_ctx        = pr_ctx,
      repo          = "owner/repo",
      post_to_github = True,
  )
"""
from __future__ import annotations

import json
from pydoc import text
from pydoc import text
from typing import TypedDict
from pathlib import Path
from datetime import datetime
from typing import TypedDict, Optional
REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(exist_ok=True)



try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

from ingestion.github_loader import PRFile
from agents.reviewer_agent import _deduplicate_findings
from agents.security_agent import SecurityAgent, merge_findings
from agents.cve_agent import CVEAgent
from agents.incremental_agent import IncrementalAgent
from memory.review_memory import ReviewMemory
from notifications.notifier import Notifier
from rag.retriever import Retriever
from prompts.prompts import build_prompt,SYSTEM_PROMPT,build_security_prompt, build_summary_prompt,build_fix_prompt
from config import cfg



# ─────────────────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────────────────

class AdvancedReviewState(TypedDict):
    # Inputs
    files:            list[PRFile]
    pr_title:         str
    pr_description:   str
    pr_author:        str
    repo:             str
    pr_number:        int
    head_sha:         str
    post_to_github:   bool

    # Planner output
    plan:             list[dict]
    memory_context:   str
    previous_review: Optional[dict]
    is_rereview:      bool

    # Executor output
    file_reviews:     list[dict]
    all_findings:     list[dict]

    # CVE output
    enriched_findings: list[dict]

    # Auto-fix output
    fixes:            dict   # filename → {line → fixed_code}

    # Publisher output
    overall_score:    float
    approved:         bool
    executive:        dict
    report:           dict
    posted:           bool
    comparison: Optional[dict]


# ─────────────────────────────────────────────────────────────────────────────
# SHARED SERVICES (initialised once, reused across nodes)
# ─────────────────────────────────────────────────────────────────────────────

_retriever  = None
_security   = None
_cve        = None
_memory     = None
_incremental= None
_notifier   = None

def _get_services():
    global _retriever, _security, _cve, _memory, _incremental, _notifier
    if _retriever   is None: _retriever   = Retriever()
    if _security    is None: _security    = SecurityAgent()
    if _cve         is None: _cve         = CVEAgent()
    if _memory      is None: _memory      = ReviewMemory()
    if _incremental is None: _incremental = IncrementalAgent()
    if _notifier    is None: _notifier    = Notifier()
    return _retriever, _security, _cve, _memory, _incremental, _notifier


# ─────────────────────────────────────────────────────────────────────────────
# NODE 1 — PLANNER
# ─────────────────────────────────────────────────────────────────────────────

def planner_node(state: AdvancedReviewState) -> AdvancedReviewState:
    print("\n[planner] Building review plan...")
    _, _, _, memory, incremental, _ = _get_services()

    author = state.get("pr_author", "unknown")

    # Feature 8: load developer history
    mem_context = memory.get_context(author)
    if mem_context:
        print(f"[planner] Loaded memory context for author: {author}")

    # Feature 5: check for previous review of this PR
    prev = incremental.load_previous_review(state["repo"], state["pr_number"])
    is_rereview = prev is not None

    if is_rereview:
        print(f"[planner] Previous review found — this is a re-review")

    # Build file plan
    plan = []
    
    for pf in state["files"]:
        filename = getattr(pf, "filename", "")
        additions = getattr(pf, "additions", 0)

        is_security = any(
            kw in filename.lower()
            for kw in ["auth", "login", "password", "secret", "token",
                       "crypt", "hash", "key", "session", "jwt", "oauth"]
        )

        # If author has a history of security issues, flag all files for security focus
        
        mem_text = str(mem_context or "")

        if "security" in mem_text.lower() and "extra" in mem_text.lower():
            focus = "security"
        elif is_security:
            focus = "security"
        else:
            focus = "full"

        plan.append({
            "file":   filename,
            "focus":  focus,
            "reason": "security-sensitive" if is_security else f"{additions} additions",
        })
        print(f"  [planner] {filename} → {focus}")

    return {
        **state,
        "plan":            plan,
        "memory_context":  mem_context,
        "previous_review": prev,
        "is_rereview":     is_rereview,
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODE 2 — EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────

def executor_node(state: AdvancedReviewState) -> AdvancedReviewState:
    print("\n[executor] Running reviews...")
    retriever, security, _, _, _, _ = _get_services()

    file_map     = {
                    getattr(pf, "filename", f"file_{i}"): pf
                    for i, pf in enumerate(state["files"])
                    }
    file_reviews = []
    all_findings = []

    for item in state["plan"]:
        filename = item["file"]
        pf       = file_map.get(filename)
        if not pf:
            continue

        print(f"\n  [executor] {filename} (focus: {item['focus']})")

        # RAG
        context_chunks = retriever.retrieve_for_file(pf)

        # Build prompt with memory context prepended
        base_prompt = build_prompt(
            pr_file        = pf,
            context_chunks = context_chunks,
            pr_title       = state["pr_title"],
            pr_description = state["pr_description"],
        )
        full_prompt = state["memory_context"] + base_prompt

        # LLM
        llm_review = _call_llm_safe(full_prompt)
        llm_review = _validate(llm_review, filename)

        # Semgrep
        semgrep_findings = security.scan_file(pf)
        merged           = merge_findings(llm_review["findings"], semgrep_findings)

        if any(f["severity"] == "critical" for f in semgrep_findings):
            llm_review["overall_score"] = min(llm_review["overall_score"], 0.3)

        llm_review["findings"] = merged
        file_reviews.append({"file": filename, "focus": item["focus"], "review": llm_review})
        all_findings.extend(merged)

    return {
        **state,
        "file_reviews": file_reviews,
        "all_findings": _deduplicate_findings(all_findings),
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODE 3 — CVE LOOKUP (Feature 3)
# ─────────────────────────────────────────────────────────────────────────────

def cve_node(state: AdvancedReviewState) -> AdvancedReviewState:
    print("\n[cve] Enriching security findings with CVE data...")
    _, _, cve_agent, _, _, _ = _get_services()

    enriched = cve_agent.enrich_findings(list(state["all_findings"]))
    cve_count = sum(1 for f in enriched if f.get("cve_ids"))
    print(f"[cve] Enriched {cve_count} findings with CVE data")

    return {**state, "enriched_findings": enriched}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 4 — AUTO-FIX GENERATOR (Feature 1)
# ─────────────────────────────────────────────────────────────────────────────

def autofix_node(state: AdvancedReviewState) -> AdvancedReviewState:
    print("\n[autofix] Generating fix suggestions...")

    findings  = state["enriched_findings"]
    file_map  = {pf.filename: pf for pf in state["files"]}
    fixes     = {}

    # Only generate fixes for critical and warning findings
    fixable = [
        f for f in findings
        if f.get("severity") in ("critical", "warning")
        and f.get("file")
        and f.get("line", 0) > 0
    ]

    if not fixable:
        print("[autofix] No fixable findings")
        return {**state, "fixes": {}}

    print(f"[autofix] Generating fixes for {len(fixable)} findings...")

    for finding in fixable[:10]:  # limit to 10 to save tokens
        filename = finding["file"]
        pf       = file_map.get(filename)
        if not pf:
            continue

        # Get surrounding code context (±5 lines around the finding)
        lines      = pf.full_content.splitlines()
        line_num   = finding.get("line", 1) - 1
        start      = max(0, line_num - 3)
        end        = min(len(lines), line_num + 5)
        snippet    = "\n".join(
            f"{i+1}: {lines[i]}" for i in range(start, end)
        )

        prompt = f"""You are a senior software engineer.

Fix this specific issue in the code below.
Return ONLY valid JSON with this exact schema — no markdown, no explanation:

{{
  "fixed_code": "<the corrected code snippet>",
  "explanation": "<one sentence: what you changed and why>",
  "line_start": <int: first line of the fix>,
  "line_end": <int: last line of the fix>
}}

ISSUE:
  Severity : {finding['severity']}
  Category : {finding['category']}
  Message  : {finding['message']}
  Fix hint : {finding.get('fix', '')}

CODE (lines {start+1}-{end}):
{snippet}
"""

        try:
            result = _call_llm_safe(prompt)
            if result.get("fixed_code"):
                if filename not in fixes:
                    fixes[filename] = []
                fixes[filename].append({
                    "line":        finding.get("line", 0),
                    "severity":    finding["severity"],
                    "original":    finding["message"],
                    "fixed_code":  result["fixed_code"],
                    "explanation": result.get("explanation", ""),
                    "line_start":  result.get("line_start", start+1),
                    "line_end":    result.get("line_end", end),
                })
                print(f"  [autofix] Fix generated for {filename}:L{finding['line']}")
        except Exception as e:
            print(f"  [autofix] Fix failed for {filename}: {e}")

    total_fixes = sum(len(v) for v in fixes.values())
    print(f"[autofix] Generated {total_fixes} fixes")
    return {**state, "fixes": fixes}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 5 — PUBLISHER (Features 4, 5)
# ─────────────────────────────────────────────────────────────────────────────

def publisher_node(state: AdvancedReviewState) -> AdvancedReviewState:
    print("\n[publisher] Building final report and posting to GitHub...")
    _, _, _, memory, incremental, _ = _get_services()

    findings     = state["enriched_findings"]
    file_reviews = state["file_reviews"]

    file_scores   = [fr["review"].get("overall_score", 1.0) for fr in file_reviews]
    overall_score = min(file_scores) if file_scores else 1.0

    critical_count = sum(1 for f in findings if f.get("severity") == "critical")

    # Feature 4: PR approval gate
    approved = overall_score >= 0.85 and critical_count == 0

    # Executive summary
    executive = {}
    any_failed = any(
        "RATE LIMITED" in fr["review"].get("summary", "")
        for fr in file_reviews
    )
    if not any_failed:
        try:
            prompt    = build_summary_prompt(file_reviews, state["pr_title"])
            executive = _call_llm_safe(prompt)
        except Exception as e:
            print(f"[publisher] Summary failed: {e}")

    report = {
        "overall_score":   round(overall_score, 2),
        "total_findings":  len(findings),
        "critical_count":  critical_count,
        "warning_count":   sum(1 for f in findings if f.get("severity") == "warning"),
        "findings":        findings,
        "files":           file_reviews,
        "fixes":           state.get("fixes", {}),
        "executive_summary": executive,
        "approved":        approved,
        "repo":            state["repo"],
        "pr_number":       state["pr_number"],
        "pr_title":        state["pr_title"],
        "pr_author":       state.get("pr_author", ""),
        "reviewed_at":     datetime.utcnow().isoformat() + "Z",
        "pipeline":        "advanced-langgraph-v2",
    }

    # Save report
    out_dir  = Path("reports")
    out_dir.mkdir(exist_ok=True)
    ts       = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"review_{state['repo'].replace('/', '_')}_pr{state['pr_number']}_{ts}.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Feature 8: record to memory
    memory.record_review(
        author     = state.get("pr_author", "unknown"),
        repo       = state["repo"],
        pr_number  = state["pr_number"],
        findings   = findings,
    )

    posted     = False
    comparison = None

    if state.get("post_to_github") and state.get("repo"):
        try:
            from ingestion.github_loader import GitHubLoader
            loader = GitHubLoader()

            # Feature 4: post with APPROVE or REQUEST_CHANGES event
            exec_text = (
                executive.get("executive_summary", "")
                or f"Reviewed {len(file_reviews)} files, {len(findings)} issues found."
            )

            loader.post_review_comments(
                repo          = state["repo"],
                pr_number     = state["pr_number"],
                head_sha      = state["head_sha"],
                findings      = findings,
                summary       = exec_text,
                approved      = approved,
            )

            # Feature 1: post auto-fix suggestions as separate comments
            _post_fix_suggestions(
                loader, state["repo"], state["pr_number"], state.get("fixes", {})
            )

            # Feature 5: post comparison if this is a re-review
            if state["is_rereview"] and state["previous_review"]:
                comparison = incremental.compare_reviews(
                    state["previous_review"], report
                )
                incremental.post_comparison_comment(
                    state["repo"], state["pr_number"], comparison
                )

            posted = True
            print("[publisher] Posted to GitHub ✅")

        except Exception as e:
            print(f"[publisher] GitHub post failed: {e}")

    # Print summary
    _print_summary(report)

    return {
        **state,
        "overall_score": overall_score,
        "approved":      approved,
        "executive":     executive,
        "report":        report,
        "posted":        posted,
        "comparison":    comparison,
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODE 6 — NOTIFIER (Feature 7)
# ─────────────────────────────────────────────────────────────────────────────

def notifier_node(state: AdvancedReviewState) -> AdvancedReviewState:
    _, _, _, _, _, notifier = _get_services()
    notifier.notify(
        report     = state["report"],
        repo       = state["repo"],
        pr_number  = state["pr_number"],
    )
    return state


# ─────────────────────────────────────────────────────────────────────────────
# BUILD GRAPH
# ─────────────────────────────────────────────────────────────────────────────

def build_advanced_graph():
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("Run: pip install langgraph langchain-core")

    graph = StateGraph(AdvancedReviewState)

    graph.add_node("planner",   planner_node)
    graph.add_node("executor",  executor_node)
    graph.add_node("cve",       cve_node)
    graph.add_node("autofix",   autofix_node)
    graph.add_node("publisher", publisher_node)
    graph.add_node("notifier",  notifier_node)

    graph.add_edge("planner",   "executor")
    graph.add_edge("executor",  "cve")
    graph.add_edge("cve",       "autofix")
    graph.add_edge("autofix",   "publisher")
    graph.add_edge("publisher", "notifier")
    graph.add_edge("notifier",  END)

    graph.set_entry_point("planner")
    return graph.compile()


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def run_advanced_review(
    pr_ctx,
    repo:            str,
    post_to_github:  bool = False,
) -> dict:
    """Main entry point. Pass a PRContext from GitHubLoader.load_pr()."""
    graph = build_advanced_graph()

    initial: AdvancedReviewState = {
        "files":            pr_ctx.files,
        "pr_title":         pr_ctx.title,
        "pr_description":   pr_ctx.description,
        "pr_author":        getattr(pr_ctx, "author", "unknown"),
        "repo":             repo,
        "pr_number":        pr_ctx.pr_number,
        "head_sha":         pr_ctx.head_sha,
        "post_to_github":   post_to_github,
        "plan":             [],
        "memory_context":   "",
        "previous_review":  None,
        "is_rereview":      False,
        "file_reviews":     [],
        "all_findings":     [],
        "enriched_findings": [],
        "fixes":            {},
        "overall_score":    1.0,
        "approved":         True,
        "executive":        {},
        "report":           {},
        "posted":           False,
        "comparison":       None,
    }

    print("[advanced] Starting 6-node LangGraph pipeline")
    print("[advanced] planner → executor → cve → autofix → publisher → notifier")
    final = graph.invoke(initial)
    return final["report"]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_groq_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=cfg.groq_api_key)
    return _groq_client

def _call_llm_safe(prompt: str) -> dict:
    try:
        client = _get_groq()
        resp   = client.chat.completions.create(
            model       = cfg.review_model,
            temperature = 0,
            max_tokens  = cfg.max_review_tokens,
            messages    = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            timeout=60,
        )
        text = resp.choices[0].message.content.strip()
        if "```" in text:
            text = "\n".join(
                l for l in text.splitlines()
                if not l.strip().startswith("```")
            )
        try:
            return json.loads(text)
        except Exception:
            start =text.find("{")
            end = text.rfind("}")

            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end+1])
                except Exception:
                    pass

            return {
            "findings": [],
            "summary": "Model returned invalid JSON",
            "overall_score": 0.5,
            "test_coverage_gaps": [],
            }
    except Exception as e:
        is_rate = "rate_limit" in str(e) or "429" in str(e)
        return {
            "findings": [],
            "summary":  f"[RATE LIMITED] {e}" if is_rate else f"Failed: {e}",
            "overall_score": 0.5 if is_rate else 0.0,
            "test_coverage_gaps": [],
        }

def _validate(review: dict, filename: str) -> dict:
    review.setdefault("findings", [])
    review.setdefault("summary", "")
    review.setdefault("test_coverage_gaps", [])
    score = float(review.get("overall_score", 1.0))
    review["overall_score"] = max(0.0, min(1.0, score))
    clean = []
    for f in review["findings"]:
        if not isinstance(f, dict) or not f.get("message"):
            continue
        f["severity"] = str(f.get("severity", "info")).lower()
        if f["severity"] not in ("critical", "warning", "info"):
            f["severity"] = "info"
        try:
            f["line"] = int(f.get("line", 0))
        except (TypeError, ValueError):
            f["line"] = 0
        f.setdefault("fix", "")
        f.setdefault("category", "style")
        f["file"] = filename
        clean.append(f)
    review["findings"] = clean
    return review

def _post_fix_suggestions(loader, repo, pr_number, fixes: dict) -> None:
    """Post auto-fix suggestions as a single summary comment."""
    if not fixes:
        return
    lines = ["## 🔧 Auto-Fix Suggestions\n"]
    lines.append("The AI generated these fixes for critical/warning issues:\n")
    for filename, file_fixes in fixes.items():
        for fix in file_fixes:
            lines.append(
                f"\n### `{filename}` — Line {fix['line']} "
                f"({fix['severity'].upper()})\n"
                f"**Issue:** {fix['original'][:100]}\n\n"
                f"**Fix:** {fix['explanation']}\n\n"
                f"```python\n{fix['fixed_code']}\n```"
            )
    body = "\n".join(lines)
    if len(body) > 60000:
        body = body[:60000] + "\n\n[truncated]"
    try:
        import requests
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers=loader.auth.headers(),
            json={"body": body},
            timeout=15,
        )
        resp.raise_for_status()
        total = sum(len(v) for v in fixes.values())
        print(f"[publisher] Posted {total} auto-fix suggestions")
    except Exception as e:
        print(f"[publisher] Auto-fix post failed: {e}")

def _print_summary(report: dict) -> None:
    score    = report.get("overall_score", 0)
    total    = report.get("total_findings", 0)
    critical = report.get("critical_count", 0)
    approved = report.get("approved", False)
    fixes    = report.get("fixes", {})
    fix_count = sum(len(v) for v in fixes.values())
    bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))

    print("\n" + "═" * 55)
    print("  Advanced LangGraph Review Complete")
    print("═" * 55)
    print(f"  Score     : [{bar}] {score:.2f}")
    print(f"  Decision  : {'✅ APPROVED' if approved else '❌ CHANGES REQUESTED'}")
    print(f"  Findings  : {total} total ({critical} critical)")
    print(f"  Auto-fixes: {fix_count} generated")
    print(f"  Pipeline  : planner → executor → cve → autofix → publisher → notifier")
    print("═" * 55)

    if critical > 0:
        print("\n  🔴 Critical Issues:")
        for f in report.get("findings", []):
            if f.get("severity") == "critical":
                cves = f.get("cve_ids", [])
                cve_str = f" [{', '.join(cves[:2])}]" if cves else ""
                print(f"    • [{f.get('file','')}:L{f.get('line',0)}] "
                      f"{str(f.get('message',''))[:70]}{cve_str}")
    print()