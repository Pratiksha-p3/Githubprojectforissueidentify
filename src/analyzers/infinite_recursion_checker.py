"""
src/analyzers/infinite_recursion_checker.py

Detects a function whose body unconditionally calls itself -- no
if/while/for/try/return/break/continue anywhere in it that could ever
stop the recursion, or reach a base case on any input. Every single
invocation recurses until Python's own recursion limit is hit --
a guaranteed RecursionError, not a "might, depending on input" finding,
since there's no input-dependent branch for it to depend on.

Deliberately conservative about what counts as "unconditional":
presence of ANY control-flow statement (if/while/for/try, or an early
return/break/continue) anywhere in the function's OWN body (not a
nested function's -- same scope-bounded walk as
undefined_name_checker.py's _is_assigned_in_own_scope(), so an if
inside an unrelated nested def doesn't wrongly clear the outer
function) disqualifies it entirely, even though most of those wouldn't
actually provide a real base case either -- the point isn't proving
every remaining case is correct, just refusing to flag anything with
even a superficial chance of terminating. Also skipped: decorated
functions (a decorator could rewrite the call entirely, e.g.
memoization, in ways this project can't reason about), generators
(`yield`/`yield from` change when the body even runs), and anything
other than a plain top-level `def`/`async def` (no lambdas, no
methods -- a method's recursive call could be overridden by a
subclass, which is exactly the kind of runtime fact this project
doesn't try to infer).

No fix is generated: the correct fix (add a base case? this function
shouldn't recurse at all?) depends entirely on what the function was
actually meant to do.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity

_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

_DISQUALIFYING_NODE_TYPES = (
    ast.If,
    ast.While,
    ast.For,
    ast.AsyncFor,
    ast.Try,
    ast.Return,
    ast.Break,
    ast.Continue,
    ast.With,
    ast.AsyncWith,
    ast.Yield,
    ast.YieldFrom,
)


def _own_scope_nodes(func: ast.AST):
    def visit(node: ast.AST, is_root: bool):
        if not is_root and isinstance(node, _SCOPE_BOUNDARY):
            return
        for child in ast.iter_child_nodes(node):
            yield child
            yield from visit(child, False)

    yield from visit(func, True)


def _calls_itself(func: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        for node in _own_scope_nodes(func)
    )


def _has_disqualifying_control_flow(func: ast.AST) -> bool:
    return any(isinstance(node, _DISQUALIFYING_NODE_TYPES) for node in _own_scope_nodes(func))


def detect_unconditional_self_recursion(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if func.decorator_list:
            continue
        if _has_disqualifying_control_flow(func):
            continue
        if not _calls_itself(func, func.name):
            continue
        if not (0 < func.lineno <= len(lines)):
            continue

        findings.append(
            Finding(
                file=filename,
                line=func.lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=(
                    f"'{func.name}' calls itself unconditionally, with no "
                    f"if/while/for/try/return anywhere in its body to ever "
                    f"stop the recursion — guaranteed RecursionError on every "
                    f"call."
                ),
                bad_code=lines[func.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="infinite_recursion_checker",
            )
        )

    return findings
