"""
prompts/prompts.py

Single home for every LLM prompt template used in the review/fix pipeline.

Previously split across three places:
  - rag/prompt.py           (review prompts — wrong folder, rag/ should be
                              retrieval logic only, not prompt text)
  - prompts/fix_prompt.py   (fix prompt — incomplete/malformed as originally
                              pasted, and duplicated by an inline f-string
                              inside agents/autofix_engine.py)
Now merged into this one module. Nothing else should define a prompt string.

Sections:
  1. REVIEW PROMPTS   — SYSTEM_PROMPT, build_prompt, build_security_prompt,
                         build_summary_prompt (moved from rag/prompt.py)
  2. FIX PROMPT        — build_fix_prompt (moved from prompts/fix_prompt.py,
                         now the only fix-prompt builder — autofix_engine.py
                         no longer builds its own inline prompt)
  3. HELPERS
"""

from __future__ import annotations

from ingestion.github_loader import PRFile
from vectordb.chroma_store import RetrievedChunk
from config import cfg


# ═════════════════════════════════════════════════════════════════════════
# 1. REVIEW PROMPTS
# ═════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are a senior software engineer performing a precise, evidence-based code review.

STRICT RULES:
1. ONLY report issues that are present in the DIFF TO REVIEW section.
   Do NOT invent issues for code that is not shown.
2. Line numbers in findings MUST correspond to lines in the diff hunk header (@@).
   If you are unsure of the exact line number, use 0.
3. The "message" field must quote the exact code snippet that caused the finding.
4. The "fix" field must contain actionable, concrete replacement code or steps.
5. overall_score is a float from 0.0 (worst) to 1.0 (best).
   Base it on: security issues (-0.3 each critical), bugs (-0.15), style (-0.05).
6. Return ONLY a valid JSON object. No markdown, no prose outside the JSON.

OUTPUT SCHEMA (follow exactly):
{
  "findings": [
    {
      "line": <int — line number in diff, or 0 if uncertain>,
      "severity": "<critical|warning|info>",
      "category": "<security|bug|style|performance|test|docs>",
      "message": "<issue description quoting the offending code>",
      "fix": "<concrete fix or replacement code>"
    }
  ],
  "summary": "<2-3 sentence review summary focusing on the most important issues>",
  "overall_score": <float 0.0–1.0>,
  "test_coverage_gaps": [
    "<specific missing test scenario>"
  ]
}

SEVERITY GUIDE:
  critical  — security vulnerability, data corruption, crash, auth bypass
  warning   — logic bug, bad practice, missing error handling
  info      — style, naming, minor improvement

If there are NO issues, return:
{
  "findings": [],
  "summary": "No issues found in this diff.",
  "overall_score": 1.0,
  "test_coverage_gaps": []
}
"""


def build_prompt(
    pr_file: PRFile,
    context_chunks: list[RetrievedChunk],
    pr_title: str = "",
    pr_description: str = "",
) -> str:

    lines: list[str] = []

    # ── PR HEADER ────────────────────────────────────────
    lines.append("=== PR INFORMATION ===")
    if pr_title:
        lines.append(f"Title: {pr_title}")
    if pr_description:
        lines.append(f"Description: {pr_description[:300]}")
    lines.append(f"File: {getattr(pr_file, 'filename', 'unknown')}")
    lines.append(f"Language: {getattr(pr_file, 'language', 'unknown')}")
    lines.append(
        f"Changes: +{getattr(pr_file, 'additions', 0)} "
        f"-{getattr(pr_file, 'deletions', 0)}"
    )
    lines.append("")

    # ── CHANGED LINES (numbered) ─────────────────────────
    changed = _extract_changed_lines_with_numbers(
        getattr(pr_file, "patch", "")
    )
    if changed:
        lines.append("=== CHANGED LINES (added/modified) ===")
        for lineno, code in changed:
            lines.append(f"  L{lineno}: {code}")
        lines.append("")

    # ── CODEBASE CONTEXT (filtered by score) ─────────────
    good_chunks = []

    for rc in (context_chunks or []):
        score = getattr(rc, "score", 0)
        if score >= cfg.min_similarity_score:
            good_chunks.append(rc)

    if good_chunks:
        lines.append("=== CODEBASE CONTEXT (similar existing code) ===")

        for idx, rc in enumerate(good_chunks[:5], start=1):

            filename = getattr(rc, "filename", "unknown")
            section_type = getattr(rc, "section_type", "")
            section_name = getattr(rc, "section_name", "")
            start_line = getattr(rc, "start_line", 0)
            end_line = getattr(rc, "end_line", 0)
            score = getattr(rc, "score", 0)
            content = getattr(rc, "content", "")

            lines.append(f"--- Context {idx} | score={score:.2f} ---")
            lines.append(f"File: {filename}  Section: {section_type} {section_name}")
            lines.append(f"Lines: {start_line}-{end_line}")

            if len(content) > 400:
                content = content[:400] + "\n...[truncated]"

            lines.append(content)
            lines.append("")

    # ── DIFF ─────────────────────────────────────────────
    lines.append("=== DIFF TO REVIEW ===")
    patch = getattr(pr_file, "patch", "") or "[No diff available]"
    if len(patch) > 2500:
        patch = patch[:2500] + "\n...[truncated]"
    lines.append(patch)
    lines.append("")

    # ── FULL FILE (truncated) ────────────────────────────
    lines.append("=== FULL FILE (for context only — do NOT review unchanged lines) ===")
    full = getattr(pr_file, "full_content", "")
    if len(full) > 1500:
        full = full[:1500] + "\n...[truncated]"
    lines.append(full)

    return "\n".join(lines)


def build_security_prompt(pr_file: PRFile) -> str:

    patch = getattr(pr_file, "patch", "")
    full_content = getattr(pr_file, "full_content", "")

    return f"""\
Review this code for SECURITY vulnerabilities ONLY.
Report ONLY issues visible in the diff — do not invent issues.

File: {pr_file.filename}
Language: {pr_file.language}

DIFF:
{patch[:4000]}

FULL FILE:
{full_content[:2000]}

Focus exclusively on:
- SQL Injection (CWE-89)
- Command Injection (CWE-78)
- Path Traversal (CWE-22)
- Hardcoded Secrets (CWE-798)
- Authentication Issues (CWE-287)
- Authorization Issues (CWE-285)
- SSRF (CWE-918)
- XSS (CWE-79)
- CSRF (CWE-352)
- Weak Crypto (CWE-327)
- Unsafe Deserialization (CWE-502)

Return VALID JSON ONLY:
{{
  "security_findings": [
    {{
      "line": <int>,
      "cwe": "CWE-89",
      "owasp": "A03 Injection",
      "severity": "critical",
      "description": "<exact code quoted + issue>",
      "exploit": "<realistic attack path>",
      "fix": "<concrete replacement code>"
    }}
  ],
  "security_score": <float 0.0–1.0>
}}
"""


def build_summary_prompt(
    file_reviews: list[dict],
    pr_title: str = "",
) -> str:
    """
    Produces a concise executive summary across all per-file reviews.
    Called once at the end of review_pr().
    """
    review_text = "\n\n".join(
        f"File: {fr['file']}\n"
        f"Score: {fr['review'].get('overall_score', '?')}\n"
        f"Findings: {len(fr['review'].get('findings', []))}\n"
        f"Summary: {fr['review'].get('summary', '')}"
        for fr in file_reviews
    )

    return f"""\
You reviewed a pull request: "{pr_title}"

Below are per-file review summaries. Write a concise PR-level executive summary.

{review_text}

Return VALID JSON ONLY:
{{
  "executive_summary": "<3-5 sentences covering the most critical issues and overall quality>",
  "top_risks": ["<risk 1>", "<risk 2>"],
  "recommended_actions": ["<action 1>", "<action 2>"],
  "approve": <true if overall_score >= 0.85 and no critical findings, else false>
}}
"""


# ═════════════════════════════════════════════════════════════════════════
# 2. FIX PROMPT
# ═════════════════════════════════════════════════════════════════════════

FIX_PROMPT_TEMPLATE = """\
You are a Senior Python Engineer fixing a specific code issue.

Issue Type: {issue_type}
Issue: {message}
File: {filename}

Code (lines {start_line}-{end_line}):
{code}

Target line {line_num}: {target_line}

Fix ONLY the target line. Return VALID JSON ONLY, no markdown fences:
{{"fixed_line": "<corrected single line, same indentation>", "explanation": "<one sentence why>"}}
"""


def build_fix_prompt(
    issue_type: str,
    message: str,
    filename: str,
    code: str,
    start_line: int,
    end_line: int,
    line_num: int,
    target_line: str,
) -> str:
    """
    Builds the LLM fix-generation prompt from a finding + surrounding code.
    Used by agents/autofix_engine.py's _llm_fix() fallback.
    """
    return FIX_PROMPT_TEMPLATE.format(
        issue_type=issue_type,
        message=message,
        filename=filename,
        code=code,
        start_line=start_line,
        end_line=end_line,
        line_num=line_num,
        target_line=target_line,
    )


# ═════════════════════════════════════════════════════════════════════════
# 3. HELPERS
# ═════════════════════════════════════════════════════════════════════════

def _extract_changed_lines_with_numbers(patch: str) -> list[tuple[int, str]]:
    """
    Parses a unified diff patch and returns (line_number, code) tuples
    for every added line (+), giving the LLM accurate line references.
    """
    import re

    result: list[tuple[int, str]] = []
    line_num = 0

    for line in patch.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                line_num = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            result.append((line_num, line[1:]))
            line_num += 1
        elif not line.startswith("-"):
            line_num += 1

    return result


# ═════════════════════════════════════════════════════════════════════════
# TEST
# ═════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class DummyPR:
        filename: str
        language: str
        patch: str
        full_content: str
        additions: int = 2
        deletions: int = 0

    pr = DummyPR(
        filename="auth.py",
        language="python",
        patch="@@ -1,0 +1,2 @@\n+password='admin'\n+print(password)",
        full_content="def login(): pass",
    )

    prompt = build_prompt(pr_file=pr, context_chunks=[], pr_title="Add Login")
    print(prompt[:2000])