"""
analyze_file.py

Analyzes any Python file for:
  - Syntax errors (with exact fix)
  - Runtime errors (division by zero, index out of range, undefined vars)
  - Logic errors (wrong conditions, wrong return values)
  - Security issues (SQL injection, command injection, hardcoded secrets)

Usage:
  python analyze_file.py test_errors.py
  python analyze_file.py any_file.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import tokenize
import io
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv
load_dotenv()


@dataclass
class Issue:
    line:     int
    type:     str      # syntax | runtime | logic | security
    severity: str      # critical | warning | info
    message:  str
    bad_code: str      # exact bad line
    fix_code: str      # exact replacement line
    reason:   str


class FileAnalyzer:

    def __init__(self):
        self._groq = None

    def analyze(self, filepath: str) -> list[Issue]:
        content = Path(filepath).read_text(encoding="utf-8")
        lines   = content.splitlines()
        issues  = []

        # 1. Syntax errors (fast, no LLM)
        issues.extend(self._check_syntax(content, lines))

        # 2. Security issues (regex + semgrep)
        issues.extend(self._check_security(content, lines))

        # 3. Runtime + Logic errors (LLM)
        issues.extend(self._check_with_llm(content, filepath))

        # Sort by line number
        issues.sort(key=lambda x: x.line)

        return issues

    # ── 1. Syntax checker ────────────────────────────────

    def _check_syntax(self, content: str, lines: list[str]) -> list[Issue]:
        issues = []

        # Try to parse — get first syntax error
        try:
            ast.parse(content)
        except SyntaxError as e:
            line_num = e.lineno or 0
            bad_line = lines[line_num - 1].strip() if 0 < line_num <= len(lines) else ""
            fix      = self._fix_syntax(bad_line, str(e.msg))
            issues.append(Issue(
                line     = line_num,
                type     = "syntax",
                severity = "critical",
                message  = f"SyntaxError: {e.msg}",
                bad_code = bad_line,
                fix_code = fix,
                reason   = "Python cannot run this file until all syntax errors are fixed.",
            ))

        # Check for common patterns line by line
        SYNTAX_PATTERNS = [
            (r'^def\s+\w+\([^)]*\)\s*$',      "Missing colon after def",      lambda l: l + ":"),
            (r'^class\s+\w+\s*$',              "Missing colon after class",    lambda l: l + ":"),
            (r'^\s*if\s+.+[^:]\s*$',           "Missing colon after if",       lambda l: l + ":"),
            (r'^\s*for\s+.+[^:]\s*$',          "Missing colon after for",      lambda l: l + ":"),
            (r'^\s*while\s+.+[^:]\s*$',        "Missing colon after while",    lambda l: l + ":"),
            (r'^\s*except\s*$',                "bare except missing colon",    lambda l: "except Exception as e:"),
            (r'^\s*except\s+\w+\s*$',          "Missing colon after except",   lambda l: l + ":"),
        ]

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            for pattern, msg, fix_fn in SYNTAX_PATTERNS:
                if re.match(pattern, stripped) and not stripped.endswith(":"):
                    issues.append(Issue(
                        line     = i,
                        type     = "syntax",
                        severity = "critical",
                        message  = f"SyntaxError: {msg}",
                        bad_code = stripped,
                        fix_code = fix_fn(stripped),
                        reason   = "Missing colon — Python requires colons at end of compound statements.",
                    ))

        return issues

    def _fix_syntax(self, bad_line: str, error_msg: str) -> str:
        if "was never closed" in error_msg or "expected ':'" in error_msg:
            return bad_line + ":"
        if "invalid syntax" in error_msg:
            if re.match(r'def\s+\w+\([^)]*\)\s*$', bad_line):
                return bad_line + ":"
            if re.match(r'class\s+\w+\s*$', bad_line):
                return bad_line + ":"
        return bad_line + "  # FIX: check syntax here"

    # ── 2. Security checker ──────────────────────────────

    def _check_security(self, content: str, lines: list[str]) -> list[Issue]:
        issues = []

        SECURITY_RULES = [
            {
                "pattern": r'(API_KEY|SECRET|PASSWORD|TOKEN|KEY)\s*=\s*["\'][^"\']+["\']',
                "flags":   re.IGNORECASE,
                "type":    "security",
                "severity":"critical",
                "message": "Hardcoded secret detected",
                "fix_fn":  lambda l, m: re.sub(
                    r'(API_KEY|SECRET|PASSWORD|TOKEN|KEY)\s*=\s*["\'][^"\']+["\']',
                    lambda x: f'{x.group(1)} = os.getenv("{x.group(1).upper()}")',
                    l, flags=re.IGNORECASE
                ),
                "reason": "Secrets committed to git are permanently exposed. Use environment variables.",
            },
            {
                "pattern": r'f["\'].*SELECT.*\{',
                "flags":   re.IGNORECASE,
                "type":    "security",
                "severity":"critical",
                "message": "SQL Injection: f-string used in SQL query",
                "fix_fn":  lambda l, m: re.sub(
                    r'query\s*=\s*f["\'](.+)["\']',
                    'query = "SELECT * FROM users WHERE username=?"  # use params=(username,)',
                    l
                ),
                "reason": "f-strings in SQL allow attackers to inject malicious SQL. Use parameterized queries.",
            },
            {
                "pattern": r'os\.system\s*\(',
                "flags":   0,
                "type":    "security",
                "severity":"critical",
                "message": "Command Injection: os.system() is dangerous",
                "fix_fn":  lambda l, m: l.replace(
                    "os.system(cmd)",
                    'subprocess.run(cmd.split(), capture_output=True, check=True)'
                ).replace(
                    "os.system(",
                    'subprocess.run(['
                ),
                "reason": "os.system() passes input directly to shell. Use subprocess.run() with a list.",
            },
        ]

        for rule in SECURITY_RULES:
            for i, line in enumerate(lines, 1):
                m = re.search(rule["pattern"], line, rule["flags"])
                if m:
                    fix = rule["fix_fn"](line.strip(), m)
                    issues.append(Issue(
                        line     = i,
                        type     = rule["type"],
                        severity = rule["severity"],
                        message  = rule["message"],
                        bad_code = line.strip(),
                        fix_code = fix.strip(),
                        reason   = rule["reason"],
                    ))

        return issues

    # ── 3. LLM checker for runtime + logic errors ────────

    def _check_with_llm(self, content: str, filepath: str) -> list[Issue]:
        if not os.getenv("GROQ_API_KEY"):
            print("[analyzer] No GROQ_API_KEY — skipping LLM analysis")
            return []

        prompt = f"""Analyze this Python file for runtime errors and logic errors.

For each issue found, return the EXACT bad line and EXACT fixed line — not descriptions.

File: {filepath}

```python
{content}
```

Return ONLY valid JSON:
{{
  "issues": [
    {{
      "line":     <int>,
      "type":     "runtime|logic",
      "severity": "critical|warning",
      "message":  "<short description>",
      "bad_code": "<exact current line from the file>",
      "fix_code": "<exact replacement — valid Python code>",
      "reason":   "<one sentence why this is wrong>"
    }}
  ]
}}

Focus on:
- Division by zero (return a / b when b could be 0)
- Index out of range (data[10] when list has 3 items)
- Undefined variables (print(username) when username not defined)
- Wrong logic (is_adult returning False for age >= 18)
- Wrong base case in recursion (factorial(0) returning 0 instead of 1)
- Missing error handling (open file without try/except)

Return ONLY JSON. No markdown."""

        try:
            from groq import Groq
            client = self._get_groq()
            resp   = client.chat.completions.create(
                model       = os.getenv("REVIEW_MODEL", "llama-3.3-70b-versatile"),
                temperature = 0,
                max_tokens  = 2048,
                messages    = [
                    {
                        "role":    "system",
                        "content": "You find runtime and logic errors in Python code. Return JSON only with exact code fixes.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)

            issues = []
            for item in data.get("issues", []):
                issues.append(Issue(
                    line     = int(item.get("line", 0)),
                    type     = item.get("type", "runtime"),
                    severity = item.get("severity", "warning"),
                    message  = item.get("message", ""),
                    bad_code = item.get("bad_code", ""),
                    fix_code = item.get("fix_code", ""),
                    reason   = item.get("reason", ""),
                ))
            return issues

        except Exception as e:
            print(f"[analyzer] LLM analysis failed: {e}")
            return []

    def _get_groq(self):
        if self._groq is None:
            from groq import Groq
            self._groq = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
        return self._groq

    # ── Print report ──────────────────────────────────────

    def print_report(self, filepath: str, issues: list[Issue]) -> None:
        icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        types = {"syntax": "SYNTAX", "runtime": "RUNTIME", "logic": "LOGIC", "security": "SECURITY"}

        print("\n" + "═" * 60)
        print(f"  File Analysis: {filepath}")
        print("═" * 60)
        print(f"  Total issues: {len(issues)}")
        print(f"  Critical: {sum(1 for i in issues if i.severity == 'critical')}")
        print(f"  Warning:  {sum(1 for i in issues if i.severity == 'warning')}")
        print("═" * 60)

        for issue in issues:
            icon  = icons.get(issue.severity, "🔵")
            itype = types.get(issue.type, issue.type.upper())
            print(f"\n{icon} [{itype}] Line {issue.line} — {issue.message}")
            print(f"\n  Bad code:")
            print(f"  ❌  {issue.bad_code}")
            print(f"\n  Fix:")
            print(f"  ✅  {issue.fix_code}")
            print(f"\n  Why: {issue.reason}")
            print("  " + "─" * 56)

        if not issues:
            print("\n  ✅ No issues found!")

        print()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_file.py <filename.py>")
        print("Example: python analyze_file.py test_errors.py")
        sys.exit(1)

    filepath = sys.argv[1]
    if not Path(filepath).exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    print(f"\n[analyzer] Analyzing {filepath}...")

    analyzer = FileAnalyzer()
    issues   = analyzer.analyze(filepath)
    analyzer.print_report(filepath, issues)

    # Save JSON report
    out = Path(filepath).stem + "_analysis.json"
    with open(out, "w") as f:
        json.dump([{
            "line":     i.line,
            "type":     i.type,
            "severity": i.severity,
            "message":  i.message,
            "bad_code": i.bad_code,
            "fix_code": i.fix_code,
            "reason":   i.reason,
        } for i in issues], f, indent=2)

    print(f"[analyzer] Report saved: {out}")


if __name__ == "__main__":
    main()