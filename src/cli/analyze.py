"""
src/cli/analyze.py

`review-cli analyze <file>` — runs every deterministic checker in
src/analyzers/registry.py against a single local file and prints the
findings. No LLM, no network calls, no GitHub — this is the fastest way
to manually verify a checker's behavior against a real file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.analyzers.registry import run_all_checkers
from src.core.models import Severity

_SEVERITY_ICON = {
    Severity.CRITICAL: "\U0001f534",
    Severity.WARNING: "\U0001f7e1",
    Severity.INFO: "\U0001f535",
}


def analyze_file(filepath: str) -> int:
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        return 1

    code = path.read_text(encoding="utf-8")
    findings = run_all_checkers(code, str(path))

    print(f"\n{'=' * 60}")
    print(f"  File Analysis: {path}")
    print(f"{'=' * 60}")
    print(f"  Total issues: {len(findings)}")
    print(f"{'=' * 60}\n")

    for f in findings:
        icon = _SEVERITY_ICON.get(f.severity, "\U0001f535")
        print(f"{icon} [{f.category.upper()}] Line {f.line} — {f.message}")
        if f.bad_code:
            print(f"  Detected: {f.bad_code}")
        if f.fix:
            print(f"  Suggested fix ({f.confidence.value} confidence):")
            for line in f.fix.splitlines():
                print(f"    {line}")
        print()

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review-cli analyze")
    parser.add_argument("file", help="Path to the file to analyze")
    args = parser.parse_args(argv)
    return analyze_file(args.file)


if __name__ == "__main__":
    sys.exit(main())
