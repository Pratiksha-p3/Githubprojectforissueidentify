# analyzers/ai_review.py
#
# Shared LLM pass used by runtime_checker.py and logic_checker.py.
#
# The regex/AST checks in those two modules are fast and free, but only
# catch the exact shapes they were written for. This module adds a second
# pass that reviews the whole file the way a senior engineer would — line
# by line, without being limited to a fixed checklist — so runtime and
# logic bugs outside the hardcoded patterns still get caught and get a
# concrete fix suggestion.
#
# One Groq call per unique file body, cached so that runtime_checker and
# logic_checker (which both scan the same file content back-to-back via
# CodeIntelMCP.scan) don't double the API cost.

from __future__ import annotations

import json
import re
from functools import lru_cache

from config import cfg

_SYSTEM_PROMPT = (
    "You are a senior software engineer with 20 years of production experience "
    "doing a line-by-line code review. You are thorough and pragmatic: you catch "
    "real bugs, not style nitpicks. Return JSON only, no markdown fences, no prose."
)

_VALID_CATEGORIES = {"runtime", "logic"}
_VALID_SEVERITY = {"critical", "warning", "info"}


def get_ai_findings(code: str, filename: str = "") -> list[dict]:
    """
    Returns a list of {line, category, severity, message, bad_code, fix, reason}
    dicts for runtime and logic issues anywhere in `code`. Empty list if no
    GROQ_API_KEY is configured or the call fails — callers should treat this
    as a best-effort supplement to their deterministic checks, never a
    hard dependency.
    """
    if not cfg.groq_api_key or not code.strip():
        return []
    try:
        return _cached_ai_findings(code)
    except Exception as e:
        print(f"[ai_review] LLM analysis failed: {e}")
        return []


@lru_cache(maxsize=64)
def _cached_ai_findings(code: str) -> list[dict]:
    from agents.llm_client import chat_completion

    prompt = f"""Review this Python code the way a senior engineer would: read it line by line
and find every RUNTIME error and every LOGIC error you can, no matter what
shape they take. Do not limit yourself to a fixed checklist — think about
what could actually break or behave wrong at execution time.

Runtime errors include (but are not limited to): division by zero, index out
of range, KeyError, AttributeError on None, calling something before it's
defined, type mismatches, unhandled exceptions around I/O or parsing,
resource leaks (files/sockets never closed), off-by-one errors that cause
crashes.

Logic errors include (but are not limited to): inverted or wrong conditions,
branches that return the wrong value, incorrect operators (e.g. multiplying
where the function should divide), wrong base cases in recursion, faulty
boolean logic, comparisons using the wrong operator.

Do NOT report syntax errors or security vulnerabilities — those are handled
elsewhere.

```python
{code}
```

Return ONLY valid JSON in this exact shape:
{{
  "findings": [
    {{
      "line":     <int, 1-indexed>,
      "category": "runtime" | "logic",
      "severity": "critical" | "warning" | "info",
      "message":  "<short description of the bug>",
      "bad_code": "<exact current line from the file>",
      "fix":      "<replacement code for that line, as valid standalone Python. Use a single line when one line is enough. When the correct fix genuinely needs more than one statement (e.g. wrapping in try/except, adding an if-guard), return multiple lines separated by \\n, each with the SAME indentation as bad_code — never join multiple statements onto one physical line with semicolons, and never split a compound statement (try/except/if/with) across a semicolon.>",
      "reason":   "<one sentence on why this is wrong>"
    }}
  ]
}}
If there are no issues, return {{"findings": []}}."""

    content = chat_completion(
        system=_SYSTEM_PROMPT,
        user=prompt,
        temperature=0,
        max_tokens=cfg.max_review_tokens,
    )
    data = _safe_json_parse(content)
    return _validate(data.get("findings", []))


def _validate(raw_findings) -> list[dict]:
    clean = []
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
        severity = str(f.get("severity", "warning")).lower()
        if severity not in _VALID_SEVERITY:
            severity = "warning"
        if not f.get("message"):
            continue
        clean.append({
            "line": line,
            "category": category,
            "severity": severity,
            "message": str(f.get("message", "")),
            "bad_code": str(f.get("bad_code", "")),
            "fix": str(f.get("fix", "")),
            "reason": str(f.get("reason", "")),
        })
    return clean


def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```[a-zA-Z]*\n?", "", text).strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"findings": []}
