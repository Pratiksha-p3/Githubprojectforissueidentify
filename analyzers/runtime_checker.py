# analyzers/runtime_checker.py

import re


def detect_runtime_errors(code, filename):
    findings = []

    patterns = [
        (
            r"/\s*0",
            "Division by zero",
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
                })

    return findings