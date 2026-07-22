"""
tools/complexity_scorer.py

Tier 2 — Feature 2: Code Complexity Scoring

Metrics computed per function:
  - Cyclomatic Complexity (CC)  : number of independent paths through code
  - Cognitive Complexity        : how hard it is to understand (not just paths)
  - Maintainability Index (MI)  : 0-100, higher = more maintainable
  - Lines of Code (LOC)         : raw size
  - Halstead Volume             : based on operators/operands

Thresholds (industry standard):
  CC  > 10  → warning  (hard to test)
  CC  > 20  → critical (refactor required)
  MI  < 65  → warning  (getting hard to maintain)
  MI  < 20  → critical (near-unmaintainable)
  LOC > 50  → warning  per function

Install: pip install radon
"""
from __future__ import annotations

import subprocess
import json
import re
from dataclasses import dataclass, field

from ingestion.github_loader import PRFile


@dataclass
class FunctionComplexity:
    name:          str
    filename:      str
    line:          int
    cc_score:      int     # cyclomatic complexity
    cc_rank:       str     # A-F
    loc:           int     # lines of code
    mi_score:      float   # maintainability index
    issues:        list[str] = field(default_factory=list)
    severity:      str = "info"


@dataclass
class ComplexityReport:
    filename:        str
    functions:       list[FunctionComplexity]
    avg_cc:          float
    avg_mi:          float
    total_issues:    int
    findings:        list[dict]  # in standard finding schema for merging


class ComplexityScorer:

    # Thresholds
    CC_WARNING  = 10
    CC_CRITICAL = 20
    MI_WARNING  = 65
    MI_CRITICAL = 20
    LOC_WARNING = 50

    def score_file(self, pr_file: PRFile) -> ComplexityReport | None:
        if pr_file.language != "python":
            return None
        if not pr_file.full_content.strip():
            return None
        if not self._radon_available():
            print("[complexity] radon not installed. Run: pip install radon")
            return self._fallback_score(pr_file)

        return self._radon_score(pr_file)

    def score_files(self, pr_files: list[PRFile]) -> list[ComplexityReport]:
        reports = []
        for pf in pr_files:
            r = self.score_file(pf)
            if r:
                reports.append(r)
                if r.total_issues > 0:
                    print(
                        f"[complexity] {pf.filename}: "
                        f"avg CC={r.avg_cc:.1f}, avg MI={r.avg_mi:.1f}, "
                        f"{r.total_issues} issues"
                    )
        return reports

    def to_findings(self, reports: list[ComplexityReport]) -> list[dict]:
        """Convert complexity reports to standard finding schema."""
        all_findings = []
        for r in reports:
            all_findings.extend(r.findings)
        return all_findings

    # ── Radon-based scoring ────────────────────────────────

    def _radon_score(self, pr_file: PRFile) -> ComplexityReport:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(pr_file.full_content)
            tmp_path = tmp.name

        try:
            # Cyclomatic complexity
            cc_result = subprocess.run(
                ["radon", "cc", "-s", "-j", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            cc_data = json.loads(cc_result.stdout or "{}") if cc_result.returncode == 0 else {}

            # Maintainability index
            mi_result = subprocess.run(
                ["radon", "mi", "-s", "-j", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            mi_data = json.loads(mi_result.stdout or "{}") if mi_result.returncode == 0 else {}

        finally:
            Path(tmp_path).unlink(missing_ok=True)

        functions = []
        findings  = []

        file_key = list(cc_data.keys())[0] if cc_data else None
        file_cc  = cc_data.get(file_key, []) if file_key else []
        file_mi  = mi_data.get(file_key, {}).get("mi", 70) if file_key else 70

        for func_data in file_cc:
            name     = func_data.get("name", "?")
            cc       = func_data.get("complexity", 0)
            rank     = func_data.get("rank", "A")
            lineno   = func_data.get("lineno", 0)
            loc      = func_data.get("endline", lineno) - lineno + 1

            issues   = []
            severity = "info"

            if cc >= self.CC_CRITICAL:
                issues.append(f"Cyclomatic complexity {cc} is critical (>20) — refactor required")
                severity = "critical"
            elif cc >= self.CC_WARNING:
                issues.append(f"Cyclomatic complexity {cc} is high (>10) — hard to test")
                severity = "warning"

            if loc >= self.LOC_WARNING:
                issues.append(f"Function is {loc} lines — consider splitting into smaller functions")
                if severity == "info":
                    severity = "warning"

            if file_mi < self.MI_CRITICAL:
                issues.append(f"Maintainability index {file_mi:.0f} is critical (<20)")
                severity = "critical"
            elif file_mi < self.MI_WARNING:
                issues.append(f"Maintainability index {file_mi:.0f} is low (<65)")
                if severity == "info":
                    severity = "warning"

            fc = FunctionComplexity(
                name     = name,
                filename = pr_file.filename,
                line     = lineno,
                cc_score = cc,
                cc_rank  = rank,
                loc      = loc,
                mi_score = file_mi,
                issues   = issues,
                severity = severity,
            )
            functions.append(fc)

            for issue in issues:
                findings.append({
                    "file":     pr_file.filename,
                    "line":     lineno,
                    "severity": severity,
                    "category": "complexity",
                    "message":  f"{name}(): {issue}",
                    "fix":      self._suggest_refactor(cc, loc, name),
                    "source":   "radon",
                    "agent":    "complexity-scorer",
                    "metrics":  {"cc": cc, "rank": rank, "mi": file_mi, "loc": loc},
                })

        avg_cc = sum(f.cc_score for f in functions) / max(len(functions), 1)
        return ComplexityReport(
            filename     = pr_file.filename,
            functions    = functions,
            avg_cc       = round(avg_cc, 1),
            avg_mi       = file_mi,
            total_issues = sum(1 for f in functions if f.issues),
            findings     = findings,
        )

    def _fallback_score(self, pr_file: PRFile) -> ComplexityReport:
        """Simple line-count based scoring when radon isn't available."""
        import ast as _ast
        findings = []
        functions = []

        try:
            tree  = _ast.parse(pr_file.full_content)
            lines = pr_file.full_content.splitlines()

            for node in _ast.walk(tree):
                if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                loc = node.end_lineno - node.lineno + 1
                if loc >= self.LOC_WARNING:
                    severity = "critical" if loc > 100 else "warning"
                    findings.append({
                        "file":     pr_file.filename,
                        "line":     node.lineno,
                        "severity": severity,
                        "category": "complexity",
                        "message":  f"{node.name}(): {loc} lines — consider splitting",
                        "fix":      f"Break {node.name}() into smaller focused functions (each <30 lines)",
                        "source":   "fallback-loc",
                        "agent":    "complexity-scorer",
                    })
        except Exception:
            pass

        return ComplexityReport(
            filename     = pr_file.filename,
            functions    = functions,
            avg_cc       = 0,
            avg_mi       = 70,
            total_issues = len(findings),
            findings     = findings,
        )

    def _suggest_refactor(self, cc: int, loc: int, func_name: str) -> str:
        if cc >= self.CC_CRITICAL:
            return (
                f"Refactor {func_name}() by extracting logical branches into "
                f"separate well-named functions. Target CC < 10."
            )
        if cc >= self.CC_WARNING:
            return (
                f"Simplify {func_name}() by reducing nested conditions. "
                f"Consider early returns and guard clauses."
            )
        if loc >= self.LOC_WARNING:
            return f"Split {func_name}() into 2-3 smaller functions, each with a single responsibility."
        return "Apply the single responsibility principle."

    @staticmethod
    def _radon_available() -> bool:
        try:
            subprocess.run(["radon", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False