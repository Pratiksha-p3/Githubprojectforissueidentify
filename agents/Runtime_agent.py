class RuntimeAgent:

    PATTERNS = [
        ("ZeroDivisionError", r"/\s*0"),
        ("IndexError", r"\[\d+\]"),
        ("NameError", r"print\([A-Za-z_]+\)"),
        ("FileNotFoundError", r'open\(".*"\)')
    ]

    def scan(self, file):

        findings = []

        lines = file.full_content.splitlines()

        for i, line in enumerate(lines, 1):

            if "/ 0" in line:
                findings.append({
                    "category": "runtime",
                    "severity": "warning",
                    "file": file.filename,
                    "line": i,
                    "message": "Division by zero"
                })

        return findings