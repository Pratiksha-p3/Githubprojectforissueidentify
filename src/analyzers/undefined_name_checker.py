"""
src/analyzers/undefined_name_checker.py

Detects a name referenced with no binding anywhere it could resolve
from (the enclosing function, an outer enclosing function, module
scope, or Python's builtins) -- raises NameError the moment that code
actually executes.

Every other checker in this package works by matching a local AST
*shape* (a call, an assignment, a subscript) -- detecting this correctly
needs something fundamentally different: a real symbol table, walking
out through every enclosing scope and accounting for closures,
comprehension scoping, `global`/`nonlocal`, conditional imports, star
imports, and more. Hand-rolling that from scratch is how you get
something with worse edge-case coverage than a tool that already solves
it -- this wraps pyflakes's Checker (the same analysis `ruff` itself
uses internally for undefined-name detection) rather than
reimplementing scope resolution.

Only pyflakes.messages.UndefinedName is surfaced here -- pyflakes
reports many other categories (unused imports, unused variables,
redefinitions, ...) that are legitimate but out of scope for a checker
specifically about NameError.

No fix is generated: the correct resolution (define the name, fix a
typo, add a missing import, adjust scope) depends entirely on what was
actually meant, which isn't recoverable from the undefined reference
alone -- same "detection, not auto-fix" stance
src/analyzers/sql_injection_checker.py already takes for a bug class
whose safe rewrite depends on context this project doesn't have.
"""
from __future__ import annotations

import ast

from pyflakes.checker import Checker
from pyflakes.messages import UndefinedName

from src.core.models import ConfidenceTier, Finding, Severity


def detect_undefined_names(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    lines = code.splitlines()
    findings: list[Finding] = []
    for msg in Checker(tree, filename=filename).messages:
        if not isinstance(msg, UndefinedName):
            continue
        lineno = msg.lineno
        bad_code = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""

        findings.append(
            Finding(
                file=filename,
                line=lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=(
                    f"{msg.message % msg.message_args} — raises NameError the "
                    f"moment this code actually runs."
                ),
                bad_code=bad_code,
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="undefined_name_checker",
            )
        )

    return findings
