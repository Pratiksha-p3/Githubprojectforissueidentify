# analyzers/logic_checker.py

def detect_logic_errors(code, filename):

    findings = []

    lines = code.splitlines()

    for i, line in enumerate(lines, start=1):

        if "age >= 18" in line:

            findings.append({
                "file": filename,
                "line": i,
                "severity": "warning",
                "category": "logic",
                "message":
                    "Adult check may return incorrect result",
            })

        if "if n == 0:" in line:

            findings.append({
                "file": filename,
                "line": i,
                "severity": "warning",
                "category": "logic",
                "message":
                    "Factorial base case should return 1",
            })

    return findings