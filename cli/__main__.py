# cli/__main__.py
#
# Single entry point for the whole project: `python -m cli <command> ...`
#
# This is a thin dispatcher, not a rewrite. Every subcommand below still
# owns its own argparse and logic in its original file — this just routes
# to it, so `python -m cli index --repo x/y` behaves exactly like
# `python index_repo.py --repo x/y` did before.
#
# Commands:
#   review          Full AI review of a GitHub PR              (app.py)
#   analyze         Analyze a single local file                (analyze_file.py)
#   index           Index a repo into ChromaDB for RAG          (index_repo.py)
#   chat            Chat about a saved PR review report         (prchat.py)
#   repochat        Chat about a GitHub repo's source code      (github_chat.py)
#   ingest-reports  Embed past review reports into ChromaDB     (ingest_reports.py)
#   serve           Start the MCP tool server                   (mcp_server.py)

from __future__ import annotations

import asyncio
import inspect
import importlib
import sys

COMMANDS = {
    "review":         "app",
    "analyze":        "analyze_file",
    "index":          "index_repo",
    "chat":           "prchat",
    "repochat":       "github_chat",
    "ingest-reports": "ingest_reports",
    "serve":          "mcp_server",
}


def print_help() -> None:
    print("AI Code Review CLI\n")
    print("Usage: python -m cli <command> [options]\n")
    print("Commands:")
    print("  review          Run full AI review on a GitHub PR      (app.py)")
    print("  analyze         Analyze a single local file             (analyze_file.py)")
    print("  index           Index a repo into ChromaDB for RAG      (index_repo.py)")
    print("  chat            Chat about a saved PR review report     (prchat.py)")
    print("  repochat        Chat about a GitHub repo's source code  (github_chat.py)")
    print("  ingest-reports  Embed past review reports into ChromaDB (ingest_reports.py)")
    print("  serve           Start the MCP tool server                (mcp_server.py)")
    print("\nRun 'python -m cli <command> --help' for command-specific options.")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print_help()
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}\n")
        print_help()
        sys.exit(1)

    module_name = COMMANDS[cmd]

    # Re-shape argv so the target script's own argparse sees what it expects,
    # e.g. `python -m cli index --repo x/y` -> the index_repo module sees
    # sys.argv == ["index_repo", "--repo", "x/y"]
    sys.argv = [module_name] + sys.argv[2:]

    module = importlib.import_module(module_name)

    # ingest_reports.py currently has no main()/guard — it runs fully at
    # import time. That still works here (import triggers it), but it means
    # `python -m cli ingest-reports --help` won't show flags because there
    # are none yet. See the cleaned-up version of that file separately.
    if hasattr(module, "main"):
        result = module.main()
        if inspect.iscoroutine(result):
            # mcp_server.py's main() is `async def main()`
            asyncio.run(result)


if __name__ == "__main__":
    main()