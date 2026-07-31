"""
src/cli/main.py

Single entry point: `review-cli <command> ...`. Only `analyze` exists at
this stage; later stages (review, index, serve, ...) add subcommands here
rather than each growing its own separate console-script entry point.
"""
from __future__ import annotations

import sys

# Windows consoles default to cp1252, which can't encode the emoji/symbols
# checker output uses — crashing mid-run with a UnicodeEncodeError. Force
# UTF-8 on stdout/stderr before anything else prints. (Same fix applied to
# the previous implementation's cli/__main__.py after hitting this live.)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from src.cli import analyze, index_repo, review, review_pr  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.core.secrets import resolve_secrets  # noqa: E402

COMMANDS = {
    "analyze": analyze.main,
    "review": review.main,
    "review-pr": review_pr.main,
    "index": index_repo.main,
}


def main() -> int:
    # No-op when SECRETS_BACKEND=env (the default) — overrides secret
    # fields from Azure Key Vault otherwise. Called once here, at the
    # single CLI entry point; a deployment running the FastAPI apps
    # directly (uvicorn src.api.webhook:app, ...) instead of through
    # this CLI would need the same call added at its own startup.
    resolve_secrets(settings)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("review-cli — AI Code Review CLI\n")
        print("Usage: review-cli <command> [options]\n")
        print("Commands:")
        print("  analyze <file>       Run deterministic checkers on a local file")
        print("  review <file>        Full orchestrator + PR-gate decision on a local file")
        print("  review-pr <repo> <pr_number>")
        print("                       Review a real open GitHub PR (dry run unless --post)")
        print("  index <directory>    Index a local directory into the RAG vector store")
        return 0 if len(sys.argv) >= 2 else 1

    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        return 1

    return COMMANDS[command](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
