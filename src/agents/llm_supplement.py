"""
src/agents/llm_supplement.py

An LLM pass that supplements the deterministic checkers in src/analyzers/
for runtime/logic bugs outside their fixed shapes — the same
deterministic-first, LLM-supplement architecture that let real bugs get
caught throughout the previous implementation's lifetime even when the
LLM API was down or rate-limited.

Code is run through src/core/prompt_sanitizer.py before being
interpolated into the prompt (defense-in-depth layer 1 against prompt
injection). Every returned finding is ConfidenceTier.LOW — free-form LLM
output, never auto-appliable — and is NOT trust-checked here; that
happens once, centrally, wherever this module's findings are consumed
(src/analyzers/registry.py, src/core/orchestrator.py), via
src/core/grounding.py's is_trustworthy().

get_llm_findings_with_status() exists alongside the simpler
get_llm_findings() because "zero findings" is ambiguous on its own — it
means either "the LLM ran and found nothing" or "the LLM call itself
failed", and src/core/orchestrator.py needs to tell those apart to set an
honest ReviewStatus (DEGRADED vs. COMPLETED) rather than silently
treating a failed call as a clean pass.
"""
from __future__ import annotations

import json
import re

from src.agents.llm_client import call_llm
from src.core.config import settings
from src.core.models import ConfidenceTier, Finding, Severity
from src.core.prompt_sanitizer import sanitize_for_prompt

_SYSTEM_PROMPT = (
    "You are a senior software engineer with 20 years of production experience "
    "doing a line-by-line code review. You are thorough and pragmatic: you catch "
    "real bugs, not style nitpicks. Return JSON only, no markdown fences, no prose."
)

_VALID_CATEGORIES = {"runtime", "logic"}
_VALID_SEVERITIES = {"critical", "warning", "info"}


def get_llm_findings(code: str, filename: str) -> list[Finding]:
    """
    Best-effort supplement to the deterministic checks — never a hard
    dependency. An empty list here means "the LLM found nothing" or "the
    LLM call failed"; those are deliberately indistinguishable through
    this simpler entry point. Callers that need to tell them apart (e.g.
    src/core/orchestrator.py, to set an honest ReviewStatus) should use
    get_llm_findings_with_status() instead.
    """
    findings, _succeeded = get_llm_findings_with_status(code, filename)
    return findings


def get_llm_findings_with_status(code: str, filename: str) -> tuple[list[Finding], bool]:
    """
    Same as get_llm_findings(), but also returns whether the LLM pass
    actually completed — False if the API call raised, or if the
    response couldn't be parsed as the expected JSON shape at all. A
    successful call that genuinely found nothing still returns ([], True).
    """
    if not code.strip():
        return [], True

    prompt = _build_prompt(sanitize_for_prompt(code))

    try:
        raw = call_llm(
            system=_SYSTEM_PROMPT,
            user=prompt,
            temperature=0,
            max_tokens=settings.max_review_tokens,
        )
    except Exception as e:
        print(f"[llm_supplement] LLM call failed: {e}")
        return [], False

    data, parsed_ok = _safe_json_parse(raw)
    if not parsed_ok:
        print(f"[llm_supplement] Could not parse LLM response as JSON: {raw!r}")

    findings = _to_findings(data.get("findings", []), filename)
    return findings, parsed_ok


def _build_prompt(code: str) -> str:
    return f"""Review this code the way a senior engineer would: read it line by line
and find every RUNTIME error and every LOGIC error you can, no matter what
shape they take. Do not limit yourself to a fixed checklist.

Do NOT report syntax errors or security vulnerabilities — those are
handled elsewhere.

```
{code}
```

Return ONLY valid JSON in this exact shape:
{{
  "findings": [
    {{
      "line": <int, 1-indexed>,
      "category": "runtime" | "logic",
      "severity": "critical" | "warning" | "info",
      "message": "<short description of the bug>",
      "bad_code": "<exact current line from the file, verbatim>",
      "fix": "<replacement code, valid standalone Python, same indentation as bad_code>"
    }}
  ]
}}
If there are no issues, return {{"findings": []}}."""


def _safe_json_parse(text: str) -> tuple[dict, bool]:
    text = text.strip()
    text = re.sub(r"```[a-zA-Z]*\n?", "", text).strip("`").strip()
    try:
        parsed = json.loads(text)
        return (parsed, True) if isinstance(parsed, dict) else ({"findings": []}, False)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return (parsed, True) if isinstance(parsed, dict) else ({"findings": []}, False)
            except json.JSONDecodeError:
                pass
        return {"findings": []}, False


def _to_findings(raw_findings: object, filename: str) -> list[Finding]:
    if not isinstance(raw_findings, list):
        return []

    findings: list[Finding] = []
    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        try:
            line = int(f.get("line", 0))
        except (TypeError, ValueError):
            continue
        if line < 1:
            continue

        category = str(f.get("category", "")).lower()
        if category not in _VALID_CATEGORIES:
            continue

        severity_raw = str(f.get("severity", "warning")).lower()
        severity = severity_raw if severity_raw in _VALID_SEVERITIES else "warning"

        message = str(f.get("message", ""))
        if not message:
            continue

        findings.append(
            Finding(
                file=filename,
                line=line,
                category=category,
                severity=Severity(severity),
                message=message,
                bad_code=str(f.get("bad_code", "")),
                fix=str(f.get("fix", "")),
                confidence=ConfidenceTier.LOW,
                source="llm_supplement",
            )
        )
    return findings
