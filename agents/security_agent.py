"""
agents/security_agent.py

Deterministic security scanning using Semgrep.
Runs ALONGSIDE the LLM reviewer — not instead of it.

Why this matters:
  The LLM reviewer can hallucinate or miss security issues.
  Semgrep uses static analysis rules (no LLM, no hallucination) to catch
  known vulnerability PATTERNS: SQL injection, hardcoded secrets, weak
  crypto, command injection, etc.

  Findings from both sources are merged. Semgrep findings get a
  confidence boost since they're rule-based, not generated.

Install: pip install semgrep
  (Semgrep also needs to be on PATH — pip install puts it there automatically)
"""
from __future__ import annotations

import cmd
import json
import shutil
import subprocess
import tempfile
from pathlib import Path



from ingestion.github_loader import PRFile


# Semgrep's free registry rulesets — much stronger coverage than "auto" alone.
# "auto" only pulls a thin default set; p/ rulesets are community-maintained
# and specifically target security issues, secrets, and language-specific bugs.
#
# Free-tier registry rules miss some patterns (notably f-string SQL injection,
# which is gated behind paid "Semgrep Code"). We fill that gap with a local
# custom rule file at security_rules/custom_rules.yaml.
import os as _os
_CUSTOM_RULES_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "security_rules", "custom_rules.yaml",
)

SEMGREP_CONFIG = ["p/security-audit", "p/secrets", "p/python", "p/owasp-top-ten"]
if _os.path.exists(_CUSTOM_RULES_PATH):
    SEMGREP_CONFIG.append(_CUSTOM_RULES_PATH)

# Map our severity scale to Semgrep's
_SEVERITY_MAP = {
    "ERROR":   "critical",
    "WARNING": "warning",
    "INFO":    "info",
}


class SecurityAgent:
    """
    Runs Semgrep against changed files and returns structured findings
    in the SAME schema as ReviewerAgent findings, so they can be merged.
    """

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self._available = self._check_semgrep_installed()

    def scan_file(self, pr_file: PRFile) -> list[dict]:
        """
        Writes the file's full content to a temp file and runs Semgrep on it.
        Returns findings only for lines that are part of the diff (changed_lines),
        to keep results focused on what the PR actually introduced.
        """
        if not self._available:
            print("[security] Semgrep not installed — skipping static scan. "
                  "Run: pip install semgrep")
            return []

        if not pr_file.full_content.strip():
            return []

        suffix = Path(pr_file.filename).suffix or ".txt"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(pr_file.full_content)
            tmp_path = tmp.name

        try:
            raw_findings = self._run_semgrep(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        changed_line_set = self._changed_line_numbers(pr_file)

        findings = []
        for rf in raw_findings:
            line = rf.get("start", {}).get("line", 0)

            print(
        f"[DEBUG] Semgrep issue at line {line} "
        f"(changed lines={sorted(changed_line_set)})"
    )

    # TEMP: disable diff filtering
    # if changed_line_set and line not in changed_line_set:
    #     continue

            severity = _SEVERITY_MAP.get(
                rf.get("extra", {}).get("severity", "WARNING"), "warning"
            )

            findings.append({
                "file": pr_file.filename,
                "line": line,
                "severity": severity,
                "category": "security",
                "message": rf.get("extra", {}).get("message", rf.get("check_id", "")),
                "fix": self._suggest_fix(rf),
                "source": "semgrep",          # marks this as deterministic, not LLM
                "rule_id": rf.get("check_id", ""),
                "confidence": 0.95,             # static analysis = high confidence
                "risk_weight": (
        50 if severity == "critical"
        else 10 if severity == "warning"
        else 2
    ),
            })

        if findings:
            print(f"[security] Semgrep found {len(findings)} issues in {pr_file.filename}")

        return findings

    def scan_files(self, files: list[PRFile]) -> list[dict]:
        all_findings = []
        for pf in files:
            all_findings.extend(self.scan_file(pf))
        return all_findings

    # ── Internals ─────────────────────────────────────────

    def _run_semgrep(self, file_path: str) -> list[dict]:
        try:
            semgrep_cmd = shutil.which("semgrep")

            if not semgrep_cmd:
                raise FileNotFoundError("semgrep executable not found")

            cmd = [semgrep_cmd, "scan"]
            # Support both a single config string and a list of configs
            configs = SEMGREP_CONFIG if isinstance(SEMGREP_CONFIG, list) else [SEMGREP_CONFIG]
            for c in configs:
                cmd.extend(["--config", c])
            cmd.extend([
                "--json",
                "--quiet",
                "--timeout", str(self.timeout),
                file_path,
            ])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 180,
            )
          

            print(f"[security] Running: {' '.join(cmd)}")
            print(f"[security] Return code: {result.returncode}")

            if result.stderr:
                print(f"[security] STDERR:\n{result.stderr[:1000]}")

            if result.stdout:
                print(f"[security] STDOUT:\n{result.stdout[:1000]}")
                
            if result.returncode not in (0, 1):  # 1 = findings exist, still success
                print(f"[security] Semgrep error: {result.stderr[:300]}")
                return []

            data = json.loads(result.stdout or "{}")
            return data.get("results", [])

        except subprocess.TimeoutExpired:
            print("[security] Semgrep scan timed out")
            return []
        except json.JSONDecodeError:
            print("[security] Semgrep returned invalid JSON")
            return []
        except FileNotFoundError:
            self._available = False
            return []

    def _suggest_fix(self, rf: dict) -> str:
        """Extract Semgrep's fix suggestion if available, else generic guidance."""
        fix = rf.get("extra", {}).get("fix", "")
        if fix:
            return fix
        rule_id = rf.get("check_id", "")
        if "sql-injection" in rule_id.lower():
            return "Use parameterized queries instead of string formatting."
        if "hardcoded" in rule_id.lower() or "secret" in rule_id.lower():
            return "Move secret to environment variable or secrets manager."
        if "weak-crypto" in rule_id.lower() or "md5" in rule_id.lower():
            return "Use bcrypt, scrypt, or argon2 for password hashing."
        return "Review this code against OWASP guidelines for this issue type."

    def _changed_line_numbers(self, pr_file: PRFile) -> set[int]:
        """Reuse the same diff-parsing logic as the rest of the pipeline."""
        import re
        patch = getattr(pr_file, "patch", "")
        if not patch:
            return set()

        changed = set()
        line_num = 0
        for line in patch.splitlines():
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                if m:
                    line_num = int(m.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                changed.add(line_num)
                line_num += 1
            elif not line.startswith("-"):
                line_num += 1
        return changed

    @staticmethod
    def _check_semgrep_installed() -> bool:
        """Check if Semgrep is installed and on PATH."""
        semgrep_path = shutil.which("semgrep")

        print(f"[security] Semgrep path: {semgrep_path}")

        return semgrep_path is not None


# ─────────────────────────────────────────────────────────────────────────────
# MERGE HELPER — combine LLM findings + Semgrep findings, dedupe
# ─────────────────────────────────────────────────────────────────────────────

def merge_findings(llm_findings, semgrep_findings):
    semgrep_keys = {(f["file"], f["line"]) for f in semgrep_findings}

    merged = list(semgrep_findings)

    for f in llm_findings:

        message = str(f.get("message", "")).lower()
        fix = str(f.get("fix", "")).lower()

        # Ignore false positives for env vars
        if (
            "os.getenv(" in message
            or "environment variable" in message
            or "loaded from env" in message
            or "os.getenv(" in fix
        ):
            print(
                f"[review-filter] Skipping env-var finding: "
                f"{f.get('file')}:{f.get('line')}"
            )
            continue

        key = (f.get("file", ""), f.get("line", 0))

        if key in semgrep_keys and f.get("category") == "security":
            continue

        merged.append(f)

    return merged


if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class DummyPR:
        filename: str
        language: str
        patch: str
        full_content: str

    sample = DummyPR(
        filename="auth.py",
        language="python",
        patch="@@ -0,0 +1,3 @@\n+password = 'admin123'\n+query = f\"SELECT * FROM users WHERE name='{user}'\"\n+import hashlib; hashlib.md5(b'x')",
        full_content="password = 'admin123'\nquery = f\"SELECT * FROM users WHERE name='{user}'\"\nimport hashlib; hashlib.md5(b'x')\n",
    )

    agent = SecurityAgent()
    findings = agent.scan_file(sample)
    print(json.dumps(findings, indent=2))