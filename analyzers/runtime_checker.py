# analyzers/runtime_checker.py

import re

def detect_runtime_errors(code, filename):

    findings = []

    patterns = [
        (
            r"/\s*0",
            "Division by zero"
        ),

        (
            r"print\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)",
            "Possible undefined variable"
        ),

        (
            r"open\s*\(",
            "File may not exist"
        ),

        (
            r"\[[0-9]+\]",
            "Possible IndexError"
        )
    ]

    for i, line in enumerate(code.splitlines(), start=1):

        for pattern, msg in patterns:

            if re.search(pattern, line):

                findings.append({
                    "file": filename,
                    "line": i,
                    "severity": "warning",
                    "category": "runtime",
                    "message": msg,
                })

    return findings