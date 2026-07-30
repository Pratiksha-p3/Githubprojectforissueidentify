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

from src.cli import analyze, review  # noqa: E402

COMMANDS = {
    "analyze": analyze.main,
    "review": review.main,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("review-cli — AI Code Review CLI\n")
        print("Usage: review-cli <command> [options]\n")
        print("Commands:")
        print("  analyze <file>   Run deterministic checkers on a local file")
        print("  review <file>    Full orchestrator + PR-gate decision on a local file")
        return 0 if len(sys.argv) >= 2 else 1

    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        return 1

    return COMMANDS[command](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
