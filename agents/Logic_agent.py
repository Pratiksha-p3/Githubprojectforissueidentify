class LogicAgent:

    def scan(self, file):

        findings = []

        lines = file.full_content.splitlines()

        for i, line in enumerate(lines, 1):

            if "return False" in line:
                findings.append({
                    "category": "logic",
                    "severity": "warning",
                    "file": file.filename,
                    "line": i,
                    "message": "Possible inverted boolean logic"
                })

            if "return 0" in line:
                findings.append({
                    "category": "logic",
                    "severity": "warning",
                    "file": file.filename,
                    "line": i,
                    "message": "Factorial base case should return 1"
                })

        return findings