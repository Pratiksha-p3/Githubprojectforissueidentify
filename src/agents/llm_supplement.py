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
output, never auto-appliable — and is NOT grounding-checked here; that
happens once, centrally, in src/analyzers/registry.py, which is the only
place a finding is allowed to leave the analysis layer.
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
    LLM call failed"; those are deliberately indistinguishable at this
    layer. Stage 3's orchestrator is what turns a failure here into an
    honest ReviewStatus.DEGRADED rather than silently treating it as a
    clean pass.
    """
    if not code.strip():
        return []

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
        return []

    data = _safe_json_parse(raw)
    return _to_findings(data.get("findings", []), filename)


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


def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```[a-zA-Z]*\n?", "", text).strip("`").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"findings": []}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return parsed if isinstance(parsed, dict) else {"findings": []}
            except json.JSONDecodeError:
                pass
        return {"findings": []}


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
