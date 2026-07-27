# analyzers/division_guard_checker.py
"""
Detects division by a bare variable with no zero check, and generates a
fix that guards the ACTUAL denominator — replacing a bare
`r"/(?!/)\\s*[a-zA-Z_]\\w*\\b"` regex whose fix template hardcoded
`if b == 0: raise ValueError(...); return a / b` regardless of what the
real expression was.

That non-contextual template caused a worse problem than just a wrong
suggestion: once someone applied it, the file literally contained
"return a / b" (the template's own placeholder text). On the next
review, the same regex matched THAT line too ("/ b" is still "division
by a variable"), producing the identical finding again — the same
"fix" re-suggested forever, since nothing checked whether the division
was already guarded.

This version works from the AST, guards the real denominator name, and
skips any division that's already preceded by an `if <denominator> ...`
check in the same function — so a guard that's actually been applied
stops being re-flagged.
"""
from __future__ import annotations

import ast


def _owning_function(parent_map: dict, node: ast.AST):
    n = parent_map.get(id(node))
    while n is not None:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return n
        n = parent_map.get(id(n))
    return None


def _already_guarded(scope_node: ast.AST, denom_name: str, before_line: int) -> bool:
    """Any `if` test in scope, before before_line, that mentions the
    denominator — deliberately permissive (any check involving the name
    counts, not just an exact `== 0`) so a real guard is never re-flagged,
    at the cost of occasionally missing an unrelated check that doesn't
    actually protect the division. Also true if it's already inside a
    try/except (ZeroDivisionError | Exception | ...)."""
    for node in ast.walk(scope_node):
        if isinstance(node, ast.If) and node.lineno < before_line:
            names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            if denom_name in names:
                return True
        if isinstance(node, ast.Try) and node.lineno < before_line:
            body_lines = {getattr(n, "lineno", 0) for n in ast.walk(node)}
            if before_line in body_lines or any(l >= before_line for l in body_lines):
                if node.handlers:
                    return True
    return False


def detect_unguarded_division(code: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    parent_map: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    def enclosing_stmt(node):
        n = node
        while n is not None and not isinstance(n, ast.stmt):
            n = parent_map.get(id(n))
        return n

    findings = []
    seen_lines = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        if not isinstance(node.right, ast.Name):
            continue  # `a / b.count` or `a / f()` need different handling; not this pattern

        denom = node.right.id
        stmt = enclosing_stmt(node)
        if stmt is None:
            continue
        line = stmt.lineno
        if line in seen_lines or not (0 < line <= len(lines)):
            continue

        scope = _owning_function(parent_map, node) or tree
        if _already_guarded(scope, denom, before_line=line):
            continue

        original = lines[line - 1]
        indent = " " * (len(original) - len(original.lstrip()))
        check = (
            f'{indent}if {denom} == 0:\n'
            f'{indent}    raise ValueError("Division by zero: \'{denom}\' is zero")'
        )
        findings.append({
            "category": "runtime",
            "severity": "warning",
            "file": filename,
            "line": line,
            "message": (
                f"Division by '{denom}' has no zero check — raises "
                f"ZeroDivisionError if '{denom}' is 0."
            ),
            "bad_code": original.strip(),
            "fix_type": "division_guard",
            "fix": f"{check}\n{original}",
            "reason": f"Inserted a zero check for '{denom}' before the division on this line.",
        })
        seen_lines.add(line)

    return findings
