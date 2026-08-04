"""
src/cli/explain.py

`review-cli explain <name>` — looks up the general remediation for an
error/vulnerability category in src/core/remediation_guide.py, whether
or not any checker in src/analyzers/registry.py actually detects it.
Exists specifically for the categories this project can't safely
auto-fix (no AST shape exists, or the correct fix depends on context
this project has no way to infer) -- "not auto-fixed" shouldn't mean
"no guidance available" when a human asks directly.
"""
from __future__ import annotations

import argparse
import difflib
import sys

from src.core.remediation_guide import REMEDIATION_GUIDE, get_remediation


def explain(name: str) -> int:
    guidance = get_remediation(name)
    if guidance is not None:
        print(f"{name}:\n  {guidance}")
        return 0

    print(f"'{name}' isn't in the remediation catalog.")
    close = difflib.get_close_matches(name, REMEDIATION_GUIDE.keys(), n=5, cutoff=0.4)
    if close:
        print("Did you mean:")
        for candidate in close:
            print(f"  {candidate}")
    else:
        print(f"Run 'review-cli explain --list' to see all {len(REMEDIATION_GUIDE)} entries.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review-cli explain")
    parser.add_argument("name", nargs="?", help="Error/vulnerability category, e.g. KeyError")
    parser.add_argument(
        "--list", action="store_true", help="List every category in the catalog"
    )
    args = parser.parse_args(argv)

    if args.list or not args.name:
        for key in REMEDIATION_GUIDE:
            print(key)
        return 0

    return explain(args.name)


if __name__ == "__main__":
    sys.exit(main())
