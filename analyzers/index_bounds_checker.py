# analyzers/index_bounds_checker.py
"""
Detects literal-index subscript accesses on the same variable
(parts[0], parts[1], parts[2], ...) that have no bounds check, and
generates ONE consolidated finding + fix per (scope, variable) instead
of one duplicate finding per access.

The old approach (a `\\[[0-9]+\\]` regex in runtime_checker.py) matched
every bracketed literal index independently, so a 3-line unpack like
`year = parts[0]; month = parts[1]; day = parts[2]` produced three
separate "Possible IndexError" findings — and its fix template was a
non-contextual stub referencing undefined `items`/`index` names, not the
actual variable, so it could never be a real fix. This module walks the
AST, groups accesses by (enclosing function, base variable name), and
where there's provably no static guarantee of enough elements, proposes
a single `if len(x) < N: raise ValueError(...)` inserted right after the
variable's defining assignment (or right before the first access if no
local assignment is found).
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


def _is_literal_nonneg_int_index(node) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value >= 0
    )


def _closest_assignment(scope_node: ast.AST, var_name: str, before_line: int):
    """Most recent `var_name = ...` assignment in scope, strictly before
    before_line — the statement whose result the indexed accesses read."""
    best = None
    for node in ast.walk(scope_node):
        if not isinstance(node, ast.Assign):
            continue
        if node.lineno >= before_line:
            continue
        if not any(isinstance(t, ast.Name) and t.id == var_name for t in node.targets):
            continue
        if best is None or node.lineno > best.lineno:
            best = node
    return best


def detect_index_bounds_issues(code: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    parent_map: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    groups: dict[tuple, list] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
            continue
        if not _is_literal_nonneg_int_index(node.slice):
            continue
        scope = _owning_function(parent_map, node)
        groups.setdefault((scope, node.value.id), []).append(node)

    findings = []
    for (scope, var_name), accesses in groups.items():
        # A single access alone isn't the "duplicate findings" problem this
        # exists to fix, and flagging every lone `x[0]` would be far too
        # noisy — only a variable indexed at more than one distinct
        # position without a guard is the pattern worth a finding.
        if len({a.slice.value for a in accesses}) < 2:
            continue

        accesses.sort(key=lambda n: (n.lineno, n.col_offset))
        indices = sorted({a.slice.value for a in accesses})
        max_index = max(indices)
        scope_node = scope if scope is not None else tree

        assign = _closest_assignment(scope_node, var_name, before_line=accesses[0].lineno)

        # Provably safe: a fixed-length list/tuple literal that already
        # covers every accessed index needs no runtime check at all.
        if (assign is not None
                and isinstance(assign.value, (ast.List, ast.Tuple))
                and len(assign.value.elts) > max_index):
            continue

        if assign is not None:
            target_line = assign.lineno
        else:
            target_line = accesses[0].lineno
        if not (0 < target_line <= len(lines)):
            continue

        original = lines[target_line - 1]
        indent = " " * (len(original) - len(original.lstrip()))
        check = (
            f'{indent}if len({var_name}) < {max_index + 1}:\n'
            f'{indent}    raise ValueError(f"Expected at least {max_index + 1} '
            f'item(s) in {var_name!r}, got {{len({var_name})}}")'
        )
        fix_code = f"{original}\n\n{check}" if assign is not None else f"{check}\n{original}"

        indices_display = ", ".join(f"{var_name}[{i}]" for i in indices)
        findings.append({
            "category": "runtime",
            "severity": "warning",
            "file": filename,
            "line": target_line,
            "message": (
                f"'{var_name}' is indexed at {len(indices)} positions ({indices_display}) "
                f"with no bounds check — any of these raises IndexError if '{var_name}' "
                f"has fewer than {max_index + 1} element(s)."
            ),
            "bad_code": original.strip(),
            "fix_type": "index_bounds_check",
            "fix": fix_code,
            "reason": (
                f"Consolidated {len(accesses)} separate indexed accesses to '{var_name}' "
                f"into a single bounds check instead of one finding per access."
            ),
        })

    return findings
