# analyzers/runtime_checker.py

import re

from analyzers.ai_review import get_ai_findings


def detect_runtime_errors(code, filename):
    findings = []
    seen_lines = set()

    patterns = [
        (
    r"/(?!/)\s*[a-zA-Z_]\w*\b",
    "Possible division by variable (unguarded)",
    "division_guard",
    """
if b == 0:
    raise ValueError("Division by zero")
return a / b
""",
),
        (
            r"print\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)",
            "Possible undefined variable",
            "undefined_variable_guard",
            """
if variable_name is None:
    raise ValueError("Undefined variable")
print(variable_name)
""",
        ),
        (
            r"open\s*\(",
            "File may not exist",
            "file_exists_guard",
            """
if not os.path.exists(path):
    raise FileNotFoundError(path)
with open(path, "r") as f:
    data = f.read()
""",
        ),
        (
            r"\[[0-9]+\]",
            "Possible IndexError",
            "index_guard",
            """
if index >= len(items):
    raise IndexError("Index out of range")
value = items[index]
""",
        ),
    ]

    # ── Fast, deterministic pass — free, catches the common shapes ──────
    for line_no, line in enumerate(code.splitlines(), start=1):
        for pattern, msg, fix_type, fix_code in patterns:
            if re.search(pattern, line):
                findings.append({
                    "category": "runtime",
                    "severity": "warning",
                    "file": filename,
                    "line": line_no,
                    "message": msg,
                    "fix_type": fix_type,
                    "fix_code": fix_code,
                    "fix": fix_code.strip(),
                })
                seen_lines.add(line_no)

    # ── LLM pass — senior-engineer review for anything the patterns
    #    above don't shape-match (not limited to a fixed checklist) ────
    for f in get_ai_findings(code, filename):
        if f["category"] != "runtime" or f["line"] in seen_lines:
            continue
        findings.append({
            "category": "runtime",
            "severity": f["severity"],
            "file": filename,
            "line": f["line"],
            "message": f["message"],
            "fix_type": "ai_suggested",
            "fix_code": f["fix"],
            "fix": f["fix"],
            "bad_code": f["bad_code"],
            "reason": f["reason"],
            "source": "llm",
        })
        seen_lines.add(f["line"])

    return findings
