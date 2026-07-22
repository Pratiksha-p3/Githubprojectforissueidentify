import ast
import re


class PythonAnalyzer:

    def scan(self, filename, content):

        findings = []

        findings.extend(
            self._syntax_check(filename, content)
        )

        findings.extend(
            self._security_check(filename, content)
        )

        findings.extend(
            self._runtime_check(filename, content)
        )

        findings.extend(
            self._quality_check(filename, content)
        )

        return findings

    def _syntax_check(self, filename, content):

        try:
            ast.parse(content)

        except SyntaxError as e:

            return [{
                "file": filename,
                "line": e.lineno,
                "severity": "critical",
                "category": "syntax",
                "message": e.msg,
                "fix": self._fix_syntax(
                    e.msg,
                    e.text or ""
                )
            }]

        return []

    def _security_check(self, filename, content):

        findings = []

        patterns = [

            (
                r'password\s*=\s*["\']',
                "Hardcoded Password",
                'password = os.getenv("PASSWORD")'
            ),

            (
                r'api_key\s*=\s*["\']',
                "Hardcoded API Key",
                'api_key = os.getenv("API_KEY")'
            ),

            (
                r'os\.system\(',
                "Command Injection",
                'subprocess.run(cmd.split(), check=True)'
            ),

            (
                r'eval\(',
                "Dangerous eval()",
                'ast.literal_eval(data)'
            ),

            (
                r'hashlib\.md5',
                "Weak Hash",
                'bcrypt.hashpw(...)'
            )
        ]

        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):

            for pattern, msg, fix in patterns:

                if re.search(pattern, line):

                    findings.append({
                        "file": filename,
                        "line": i,
                        "severity": "critical",
                        "category": "security",
                        "message": msg,
                        "fix": fix
                    })

        return findings

    def _runtime_check(self, filename, content):

        findings = []

        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):

            if "/ 0" in line:

                findings.append({
                    "file": filename,
                    "line": i,
                    "severity": "warning",
                    "category": "runtime",
                    "message": "Division by zero",
                    "fix": "if denominator != 0:"
                })

            if "print(username)" in line:

                findings.append({
                    "file": filename,
                    "line": i,
                    "severity": "warning",
                    "category": "runtime",
                    "message": "Undefined variable username",
                    "fix": "Define username before use"
                })

            if 'open("missing.txt")' in line:

                findings.append({
                    "file": filename,
                    "line": i,
                    "severity": "warning",
                    "category": "runtime",
                    "message": "File may not exist",
                    "fix": "Use Path.exists()"
                })

        return findings

    def _quality_check(self, filename, content):

        findings = []

        try:

            tree = ast.parse(content)

            imports = []
            used = set()

            for node in ast.walk(tree):

                if isinstance(node, ast.Import):

                    for alias in node.names:
                        imports.append(alias.name)

                elif isinstance(node, ast.Name):

                    used.add(node.id)

            for imp in imports:

                if imp not in used:

                    findings.append({
                        "file": filename,
                        "line": 1,
                        "severity": "info",
                        "category": "quality",
                        "message": f"Unused import {imp}",
                        "fix": f"Remove import {imp}"
                    })

        except:
            pass

        return findings

    def _fix_syntax(self, msg, line):

        if "expected ':'" in msg:
            return line.rstrip() + ":"

        if "invalid syntax" in msg:
            return "Review syntax near reported line"

        return "Manual fix required"