"""
agents/multi_agent.py

Phase 2: LangGraph Multi-Agent System

Coordinator dispatches to 5 specialist agents running in parallel:

    Coordinator
         │
    ┌────┼────┬──────┬──────┐
    ▼    ▼    ▼      ▼      ▼
Security Quality Arch Perf  Docs
    │    │    │      │      │
    └────┴────┴──────┴──────┘
                │
           Aggregator
                │
         Final Decision

Each agent has its own system prompt, scoring rubric, and output schema.
Coordinator merges all scores into a weighted final verdict.

Install: pip install langgraph langchain-core
"""
from __future__ import annotations

import json
from typing import TypedDict, Annotated
import operator
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False

from config import cfg
from ingestion.github_loader import PRFile
from rag.repo_retriever import RepoAwareRetriever


# ─────────────────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────────────────

class MultiAgentState(TypedDict):
    # Inputs
    files:          list[PRFile]
    pr_title:       str
    pr_description: str
    repo:           str
    pr_number:      int
    head_sha:       str

    # Each agent writes its results here
    security_result:     dict
    quality_result:      dict
    architecture_result: dict
    performance_result:  dict
    docs_result:         dict

    # Final aggregated output
    final_report:   dict


# ─────────────────────────────────────────────────────────────────────────────
# AGENT PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

AGENT_CONFIGS = {
    "security": {
        "weight": 0.35,
        "system": """You are a security-focused code reviewer (AppSec expert).
Review ONLY for security vulnerabilities in the diff.

Focus on:
- SQL/Command/LDAP injection
- Authentication/Authorization flaws
- Hardcoded secrets/credentials
- Cryptographic weaknesses
- SSRF, XSS, CSRF
- Insecure deserialization
- Path traversal
- Sensitive data exposure

Return ONLY valid JSON:
{
  "score": <float 0.0-1.0, 1.0=no issues>,
  "findings": [
    {
      "line": <int>,
      "severity": "critical|warning|info",
      "cwe": "CWE-XX",
      "owasp": "AXX:2021",
      "message": "<exact code + issue>",
      "fix": "<secure replacement code>"
    }
  ],
  "summary": "<2 sentences>",
  "verdict": "approve|request_changes"
}""",
    },
    "quality": {
        "weight": 0.25,
        "system": """You are a code quality reviewer (senior software engineer).
Review ONLY for code quality issues in the diff.

Focus on:
- Code complexity (cyclomatic complexity > 10)
- DRY violations (duplicated logic)
- SOLID principle violations
- Error handling gaps
- Magic numbers/strings
- Function length (>50 lines = concern)
- Naming conventions
- Test coverage gaps

Return ONLY valid JSON:
{
  "score": <float 0.0-1.0>,
  "findings": [
    {
      "line": <int>,
      "severity": "warning|info",
      "category": "complexity|duplication|naming|error_handling|testing",
      "message": "<issue>",
      "fix": "<better approach>"
    }
  ],
  "summary": "<2 sentences>",
  "test_gaps": ["<missing test scenario>"],
  "verdict": "approve|request_changes"
}""",
    },
    "architecture": {
        "weight": 0.20,
        "system": """You are a software architect reviewing for design issues.
Review ONLY for architectural concerns in the diff.

Focus on:
- Layer violations (e.g. business logic in controllers)
- Tight coupling / missing abstractions
- Missing interface definitions
- Circular dependencies
- God classes / SRP violations
- Missing dependency injection
- Wrong design pattern usage
- API contract changes (breaking changes)

Return ONLY valid JSON:
{
  "score": <float 0.0-1.0>,
  "findings": [
    {
      "line": <int>,
      "severity": "warning|info",
      "pattern": "<design pattern violated>",
      "message": "<architectural issue>",
      "fix": "<better design approach>"
    }
  ],
  "summary": "<2 sentences>",
  "breaking_changes": ["<breaking change if any>"],
  "verdict": "approve|request_changes"
}""",
    },
    "performance": {
        "weight": 0.10,
        "system": """You are a performance engineer reviewing for efficiency issues.
Review ONLY for performance concerns in the diff.

Focus on:
- N+1 query patterns
- Missing database indexes (inferred from query patterns)
- Unnecessary loops / O(n²) algorithms
- Missing caching for expensive operations
- Large object creation in loops
- Blocking I/O in async contexts
- Memory leaks
- Missing pagination

Return ONLY valid JSON:
{
  "score": <float 0.0-1.0>,
  "findings": [
    {
      "line": <int>,
      "severity": "warning|info",
      "category": "database|algorithm|memory|io|caching",
      "message": "<performance issue>",
      "estimated_impact": "high|medium|low",
      "fix": "<optimized version>"
    }
  ],
  "summary": "<2 sentences>",
  "verdict": "approve|request_changes"
}""",
    },
    "docs": {
        "weight": 0.10,
        "system": """You are a technical writer reviewing documentation quality.
Review ONLY for documentation issues in the diff.

Focus on:
- Missing docstrings on public functions/classes
- Outdated comments (comments that contradict the code)
- Missing type hints (Python)
- Missing JSDoc (JavaScript/TypeScript)
- Missing README updates for new features
- Complex logic without inline explanation
- Missing error documentation

Return ONLY valid JSON:
{
  "score": <float 0.0-1.0>,
  "findings": [
    {
      "line": <int>,
      "severity": "info",
      "type": "missing_docstring|outdated_comment|missing_types|needs_explanation",
      "message": "<documentation issue>",
      "fix": "<suggested documentation>"
    }
  ],
  "summary": "<2 sentences>",
  "verdict": "approve|request_changes"
}""",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────────────────────────────────────

_groq_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=cfg.groq_api_key)
    return _groq_client


def _call_agent(agent_name: str, prompt: str) -> dict:
    """Call Groq with agent-specific system prompt."""
    system = AGENT_CONFIGS[agent_name]["system"]
    try:
        client = _get_groq()
        resp   = client.chat.completions.create(
            model       = cfg.review_model,
            temperature = 0,
            max_tokens  = 2048,
            messages    = [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        if "```" in text:
            text = "\n".join(
                l for l in text.splitlines()
                if not l.strip().startswith("```")
            )
        result       = json.loads(text)
        result["agent"] = agent_name
        return result
    except Exception as e:
        is_rate = "429" in str(e) or "rate_limit" in str(e)
        return {
            "agent":    agent_name,
            "score":    0.5 if is_rate else 0.0,
            "findings": [],
            "summary":  f"[{'RATE_LIMITED' if is_rate else 'ERROR'}] {e}",
            "verdict":  "request_changes",
        }


# ─────────────────────────────────────────────────────────────────────────────
# BUILD DIFF PROMPT (shared across all agents)
# ─────────────────────────────────────────────────────────────────────────────

def _build_diff_prompt(
    files:          list[PRFile],
    pr_title:       str,
    pr_description: str,
    context_chunks: list = None,
) -> str:
    lines = [
        f"PR TITLE: {pr_title}",
        f"PR DESCRIPTION: {pr_description[:300]}" if pr_description else "",
        "",
    ]

    # Add RAG context if available
    if context_chunks:
        lines.append("=== RELATED CODEBASE CONTEXT ===")
        for i, chunk in enumerate(context_chunks[:4], 1):
            filename    = getattr(chunk, "filename", "?")
            start_line  = getattr(chunk, "start_line", 0)
            content     = getattr(chunk, "content", "")
            reason      = getattr(chunk, "retrieval_reason", "semantic")
            lines.append(f"--- Context {i}: {filename} L{start_line} [{reason}] ---")
            lines.append(content[:600])
            lines.append("")

    lines.append("=== CHANGED FILES ===")
    for pf in files:
        lines.append(f"\nFile: {pf.filename} ({pf.language})")
        lines.append(f"Changes: +{pf.additions} -{pf.deletions}")

        patch = getattr(pf, "patch", "")
        if patch:
            lines.append("Diff:")
            lines.append(patch[:3000])

        content = getattr(pf, "full_content", "")
        if content:
            lines.append("Full file:")
            lines.append(content[:2000])

    return "\n".join(l for l in lines if l is not None)


# ─────────────────────────────────────────────────────────────────────────────
# NODES
# ─────────────────────────────────────────────────────────────────────────────

def coordinator_node(state: MultiAgentState) -> MultiAgentState:
    """
    Coordinator: builds the shared prompt and retrieves RAG context.
    All agents share the same diff + context.
    """
    print("\n[coordinator] Building shared prompt for all agents...")

    # Try to get repo-aware context
    context_chunks = []
    try:
        retriever = RepoAwareRetriever()
        for pf in state["files"]:
            chunks = retriever.retrieve_for_file(pf, top_k=4)
            context_chunks.extend(chunks)
    except Exception as e:
        print(f"[coordinator] RAG retrieval failed: {e}")

    # Store prompt in state so agents can use it
    prompt = _build_diff_prompt(
        files          = state["files"],
        pr_title       = state["pr_title"],
        pr_description = state["pr_description"],
        context_chunks = context_chunks,
    )

    print(
        f"[coordinator] Prompt ready "
        f"({len(state['files'])} files, "
        f"{len(context_chunks)} context chunks)"
    )
    print("[coordinator] Dispatching to 5 specialist agents in parallel...")

    # Run all 5 agents in parallel using ThreadPoolExecutor
    agent_names = list(AGENT_CONFIGS.keys())
    results     = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_call_agent, name, prompt): name
            for name in agent_names
        }
        for future in as_completed(futures):
            name           = futures[future]
            result         = future.result()
            results[name]  = result
            score          = result.get("score", 0)
            n_findings     = len(result.get("findings", []))
            print(
                f"  [{name:12s}] score={score:.2f}  "
                f"findings={n_findings}  "
                f"verdict={result.get('verdict','?')}"
            )

    return {
        **state,
        "security_result":     results.get("security",     {}),
        "quality_result":      results.get("quality",      {}),
        "architecture_result": results.get("architecture", {}),
        "performance_result":  results.get("performance",  {}),
        "docs_result":         results.get("docs",         {}),
    }


def aggregator_node(state: MultiAgentState) -> MultiAgentState:
    """
    Aggregator: merges all agent results into a single weighted report.
    """
    print("\n[aggregator] Merging agent results...")

    agent_results = {
        "security":     state["security_result"],
        "quality":      state["quality_result"],
        "architecture": state["architecture_result"],
        "performance":  state["performance_result"],
        "docs":         state["docs_result"],
    }

    # Weighted score
    total_weight  = 0.0
    weighted_sum  = 0.0
    score_breakdown = {}

    for name, result in agent_results.items():
        weight = AGENT_CONFIGS[name]["weight"]
        score  = float(result.get("score", 0.5))
        weighted_sum  += weight * score
        total_weight  += weight
        score_breakdown[name] = round(score, 2)

    overall_score = round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0

    # Merge all findings
    all_findings = []
    for name, result in agent_results.items():
        for f in result.get("findings", []):
            f["agent"]    = name
            f.setdefault("severity", "info")
            all_findings.append(f)

    # Sort: critical first, then by agent weight
    agent_order = list(AGENT_CONFIGS.keys())
    sev_order   = {"critical": 0, "warning": 1, "info": 2}
    all_findings.sort(
        key=lambda f: (
            sev_order.get(f.get("severity", "info"), 2),
            agent_order.index(f.get("agent", "docs"))
            if f.get("agent") in agent_order else 99,
        )
    )

    critical_count = sum(1 for f in all_findings if f.get("severity") == "critical")
    warning_count  = sum(1 for f in all_findings if f.get("severity") == "warning")

    # Approval: all agents approve AND no criticals AND score >= 0.80
    all_verdicts = [r.get("verdict", "request_changes") for r in agent_results.values()]
    approved = (
        all(v == "approve" for v in all_verdicts)
        and critical_count == 0
        and overall_score >= 0.80
    )

    # Build per-agent summaries
    agent_summaries = {
        name: {
            "score":   score_breakdown[name],
            "summary": result.get("summary", ""),
            "verdict": result.get("verdict", "?"),
        }
        for name, result in agent_results.items()
    }

    report = {
        "overall_score":     overall_score,
        "score_breakdown":   score_breakdown,
        "total_findings":    len(all_findings),
        "critical_count":    critical_count,
        "warning_count":     warning_count,
        "findings":          all_findings,
        "agent_summaries":   agent_summaries,
        "approved":          approved,
        "pipeline":          "multi-agent-langgraph",
    }

    _print_multi_agent_summary(report)
    return {**state, "final_report": report}


def _print_multi_agent_summary(report: dict) -> None:
    score = report["overall_score"]
    bar   = "█" * int(score * 20) + "░" * (20 - int(score * 20))

    print("\n" + "═" * 58)
    print("  Multi-Agent Review Complete")
    print("═" * 58)
    print(f"  Overall  : [{bar}] {score:.2f}")
    print(f"  Decision : {'✅ APPROVED' if report['approved'] else '❌ CHANGES REQUESTED'}")
    print(f"  Findings : {report['total_findings']} total "
          f"({report['critical_count']} critical, {report['warning_count']} warnings)")
    print()
    print("  Agent Scores:")
    icons = {
        "security": "🔐", "quality": "✨",
        "architecture": "🏗️", "performance": "⚡", "docs": "📝",
    }
    for name, summary in report["agent_summaries"].items():
        icon  = icons.get(name, "•")
        sc    = summary["score"]
        color = "✅" if sc >= 0.8 else "⚠️" if sc >= 0.5 else "❌"
        print(f"    {icon} {name:12s}: {sc:.2f} {color}  {summary['verdict']}")
    print("═" * 58)


# ─────────────────────────────────────────────────────────────────────────────
# BUILD GRAPH
# ─────────────────────────────────────────────────────────────────────────────

def build_multi_agent_graph():
    if not HAS_LANGGRAPH:
        raise ImportError("pip install langgraph langchain-core")

    graph = StateGraph(MultiAgentState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("aggregator",  aggregator_node)
    graph.add_edge("coordinator", "aggregator")
    graph.add_edge("aggregator",  END)
    graph.set_entry_point("coordinator")
    return graph.compile()


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def run_multi_agent_review(
    files:          list[PRFile],
    pr_title:       str = "",
    pr_description: str = "",
    repo:           str = "",
    pr_number:      int = 0,
    head_sha:       str = "",
) -> dict:
    """
    Run the full multi-agent review.
    Returns the aggregated report dict.
    """
    graph = build_multi_agent_graph()

    initial: MultiAgentState = {
        "files":              files,
        "pr_title":           pr_title,
        "pr_description":     pr_description,
        "repo":               repo,
        "pr_number":          pr_number,
        "head_sha":           head_sha,
        "security_result":    {},
        "quality_result":     {},
        "architecture_result":{},
        "performance_result": {},
        "docs_result":        {},
        "final_report":       {},
    }

    print("[multi-agent] Starting coordinator → 5 parallel agents → aggregator")
    final = graph.invoke(initial)
    return final["final_report"]


if __name__ == "__main__":
    from ingestion.github_loader import MockGitHubLoader
    pr  = MockGitHubLoader().load_pr("demo/repo", 1)
    out = run_multi_agent_review(
        files          = pr.files,
        pr_title       = pr.title,
        pr_description = pr.description,
    )
    print(json.dumps({k: v for k, v in out.items() if k != "findings"}, indent=2))