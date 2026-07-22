import ast

class SyntaxAgent:

    def scan(self, file):

        findings = []

        try:
            ast.parse(file.full_content)

        except SyntaxError as e:

            findings.append({
                "category": "syntax",
                "severity": "critical",
                "file": file.filename,
                "line": e.lineno,
                "message": e.msg,
                "fix": "Fix Python syntax"
            })

        return findings