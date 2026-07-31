"""
src/agents/_llm_finding_agent.py

Shared "call an LLM with a focused prompt, parse its findings" logic —
factored out of what src/agents/llm_supplement.py originally did inline
for runtime/logic bugs, so every specialized agent in this package
(security_agent.py, style_agent.py, test_coverage_agent.py, and
llm_supplement.py itself) supplies only its own system prompt, task
description, valid categories, and source tag, rather than each
re-implementing sanitization/JSON-parsing/validation independently.

Stage 14: this is also where the "eval-drift" half of the circuit breaker
(src/core/circuit_breaker.py) gets fed. src/agents/llm_client.py already
trips the breaker on transport-level failures (timeouts, rate limits); a
response that comes back 200 OK but fails to parse as the expected JSON
shape is a different failure mode entirely — the transport succeeded, the
model just isn't producing usable output anymore — and would otherwise
never register as a "failure" anywhere. Recording it here, against the
same shared breaker, means a model that starts silently degrading in
output quality (without ever raising an exception) still eventually trips
the breaker and gets the pipeline to fall back to deterministic-only
results, the same as any other LLM outage.
"""
from __future__ import annotations

import json
import re

from src.agents.llm_client import call_llm
from src.core.circuit_breaker import CircuitOpenError, breaker
from src.core.config import settings
from src.core.models import ConfidenceTier, Finding, Severity
from src.core.prompt_sanitizer import sanitize_for_prompt

_VALID_SEVERITIES = {"critical", "warning", "info"}


def run_finding_agent(
    code: str,
    filename: str,
    *,
    system_prompt: str,
    task_prompt: str,
    valid_categories: set[str],
    source_name: str,
    context: str = "",
    canary_key: str | None = None,
) -> tuple[list[Finding], bool]:
    """
    Returns (findings, succeeded) — succeeded is False if the LLM call
    raised (including the circuit breaker being open) or the response
    couldn't be parsed as the expected JSON shape, True otherwise
    (including "ran fine, found nothing"). Callers that need to
    distinguish "found nothing" from "call failed" should check
    `succeeded`, not just whether findings is empty.

    `canary_key` (typically f"{repo}:{commit_sha}") opts this call into
    Stage 14's canary prompt rollout (src/core/canary.py) — omitted (the
    default), every call uses the stable model exactly as before.
    """
    if not code.strip():
        return [], True

    prompt = _build_prompt(task_prompt, sanitize_for_prompt(code), context)

    try:
        raw = call_llm(
            system=system_prompt,
            user=prompt,
            temperature=0,
            max_tokens=settings.max_review_tokens,
            canary_key=canary_key,
        )
    except CircuitOpenError as e:
        print(f"[{source_name}] {e}")
        return [], False
    except Exception as e:
        print(f"[{source_name}] LLM call failed: {e}")
        return [], False

    data, parsed_ok = _safe_json_parse(raw)
    if parsed_ok:
        breaker.record_success()
    else:
        breaker.record_failure()
        print(f"[{source_name}] Could not parse LLM response as JSON: {raw!r}")

    findings = _to_findings(data.get("findings", []), filename, valid_categories, source_name)
    return findings, parsed_ok


def _build_prompt(task_prompt: str, code: str, context: str) -> str:
    context_section = (
        f"\n=== SIMILAR CODE ELSEWHERE IN THE REPO (for consistency, not the "
        f"target of this review) ===\n{context}\n"
        if context
        else ""
    )
    return f"""{task_prompt}
{context_section}
```
{code}
```

Return ONLY valid JSON in this exact shape:
{{
  "findings": [
    {{
      "line": <int, 1-indexed>,
      "category": "<category>",
      "severity": "critical" | "warning" | "info",
      "message": "<short description of the issue>",
      "bad_code": "<exact current line from the file, verbatim>",
      "fix": "<replacement code, same indentation as bad_code, or empty if none>"
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


def _to_findings(
    raw_findings: object, filename: str, valid_categories: set[str], source_name: str
) -> list[Finding]:
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
        if category not in valid_categories:
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
                source=source_name,
            )
        )
    return findings
