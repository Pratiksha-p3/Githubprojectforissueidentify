# analyzers/syntax_checker.py

import ast


def detect_syntax_errors(code, filename):
    findings = []

    try:
        ast.parse(code)
    except SyntaxError as e:
        findings.append({
            "file": filename,
            "line": e.lineno,
            "severity": "critical",
            "category": "syntax",
            "message": e.msg,
            "fix": "Fix Python syntax",
        })

    return findings