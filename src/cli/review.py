"""
src/cli/review.py

`review-cli review <file>` — runs the full orchestrator (deterministic
checkers + LLM supplement, on by default) against a single local file and
prints the resulting ReviewResult plus the PR-gate decision.

Operates on a local file rather than a real PR diff for now — diff/PR
ingestion is Stage 5's job, once GitHub integration exists. Until then
this is the fastest way to manually verify the orchestrator + gate
end to end.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.core.orchestrator import review_code
from src.core.pr_gate import GateDecision, decide, gate_reason

_DECISION_ICON = {
    GateDecision.APPROVE: "✅",
    GateDecision.BLOCK: "\U0001f534",
    GateDecision.REVIEW_REQUIRED: "⚠️",
}


def review_file(
    filepath: str,
    *,
    repo: str = "local/unknown",
    commit_sha: str = "local",
    include_llm: bool = True,
    as_json: bool = False,
) -> int:
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        return 1

    code = path.read_text(encoding="utf-8")
    result = review_code(
        code, str(path), repo=repo, commit_sha=commit_sha, include_llm=include_llm
    )
    decision = decide(result)
    reason = gate_reason(result)

    if as_json:
        payload = result.model_dump(mode="json")
        payload["gate_decision"] = decision.value
        payload["gate_reason"] = reason
        print(json.dumps(payload, indent=2))
    else:
        _print_human(path, result, decision, reason)

    return 0 if decision != GateDecision.BLOCK else 1


def _print_human(path: Path, result, decision: GateDecision, reason: str) -> None:
    icon = _DECISION_ICON[decision]
    print(f"\n{'=' * 60}")
    print(f"  Review: {path}")
    print(f"{'=' * 60}")
    print(f"  Status:   {result.status.value}")
    print(f"  Findings: {len(result.findings)} ({result.critical_count} critical)")
    print(f"  Decision: {icon} {decision.value.upper()}")
    print(f"  Reason:   {reason}")
    print(f"{'=' * 60}\n")

    for f in result.findings:
        print(f"  [{f.severity.value.upper()}] Line {f.line} — {f.message} (source: {f.source})")
        if f.fix:
            print(f"    Suggested fix ({f.confidence.value} confidence):")
            for line in f.fix.splitlines():
                print(f"      {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review-cli review")
    parser.add_argument("file", help="Path to the file to review")
    parser.add_argument("--repo", default="local/unknown", help="Repo identifier for the report")
    parser.add_argument("--commit-sha", default="local", help="Commit SHA for the report")
    parser.add_argument(
        "--no-llm", action="store_false", dest="include_llm",
        help="Skip the LLM supplement pass (deterministic checkers only)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Print JSON instead of a human summary",
    )
    args = parser.parse_args(argv)
    return review_file(
        args.file,
        repo=args.repo,
        commit_sha=args.commit_sha,
        include_llm=args.include_llm,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    sys.exit(main())
