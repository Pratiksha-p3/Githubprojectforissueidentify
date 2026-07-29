# analyzers/dict_key_checker.py
"""
Detects dict[literal_key] access with no existence guard, on a dict
whose exact contents can't be verified statically — a function
parameter (the caller could pass anything). Same shape of bug as
list-index-without-bounds-check, so this follows
index_bounds_checker.py's architecture: group every unguarded access to
the same parameter together and emit ONE consolidated finding + fix
(inserted at the top of the function body) instead of one duplicate
finding per key access.

Deliberately narrow scope, to keep false positives low:
  - Only flags PARAMETERS, not arbitrary dict-valued variables — a dict
    built locally from a literal or from `dict(...)`/comprehension has
    knowable contents; a parameter's don't.
  - Only literal string keys (`d["x"]`), not `d[some_variable]` — a
    dynamic key can't be checked against without more context anyway.
  - Skips a key already guarded by `if "x" in d`, `"x" not in d`, a
    `.get(...)` call visible anywhere on the same dict, or a surrounding
    try/except KeyError.
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


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = func.args
    names = {a.arg for a in args.args}
    names |= {a.arg for a in args.posonlyargs}
    names |= {a.arg for a in args.kwonlyargs}
    if args.vararg:
        names.discard(args.vararg.arg)  # *args is a tuple, not this dict
    if args.kwarg:
        names.discard(args.kwarg.arg)   # **kwargs is itself a dict of unknown keys — different shape of risk, not this check
    return names


def _exception_names(h_type) -> list[str]:
    """Handler exception name(s) — handles bare (`KeyError`) and dotted
    (e.g. `custom.errors.KeyError`) forms, plus tuples."""
    if isinstance(h_type, ast.Name):
        return [h_type.id]
    if isinstance(h_type, ast.Attribute):
        return [h_type.attr]
    if isinstance(h_type, ast.Tuple):
        names = []
        for e in h_type.elts:
            if isinstance(e, ast.Name):
                names.append(e.id)
            elif isinstance(e, ast.Attribute):
                names.append(e.attr)
        return names
    return []


def _already_guarded(func: ast.AST, var_name: str, key: str) -> bool:
    """Any `key in var` / `key not in var` test, a `.get(` call on var,
    or a surrounding try/except (KeyError | Exception), anywhere in the
    function — deliberately permissive, same trade-off
    division_guard_checker.py makes: never re-flag something that's
    actually guarded, even at the cost of occasionally missing a guard
    that doesn't really cover this specific access."""
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            left_is_key = isinstance(node.left, ast.Constant) and node.left.value == key
            ops_ok = any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            comparators_have_var = any(
                isinstance(c, ast.Name) and c.id == var_name for c in node.comparators
            )
            if left_is_key and ops_ok and comparators_have_var:
                return True
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == var_name):
                return True
        if isinstance(node, ast.Try):
            names = [nm for h in node.handlers for nm in _exception_names(h.type)]
            if any(nm in ("KeyError", "Exception") for nm in names):
                return True
    return False


def detect_unguarded_dict_access(code: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    parent_map: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    findings = []
    seen_funcs: set = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(func) in seen_funcs:
            continue
        seen_funcs.add(id(func))

        params = _param_names(func)
        if not params:
            continue

        by_var: dict[str, list[str]] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
                continue
            var_name = node.value.id
            if var_name not in params:
                continue
            if _owning_function(parent_map, node) is not func:
                continue  # belongs to a nested function shadowing the same param name
            key_node = node.slice
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            key = key_node.value
            if _already_guarded(func, var_name, key):
                continue
            by_var.setdefault(var_name, [])
            if key not in by_var[var_name]:
                by_var[var_name].append(key)

        for var_name, keys in by_var.items():
            target_line = func.lineno
            if not (0 < target_line <= len(lines)):
                continue
            def_line = lines[target_line - 1]
            body_indent = " " * (len(def_line) - len(def_line.lstrip()) + 4)

            keys_display = ", ".join(f"'{k}'" for k in keys)
            missing_check = " or ".join(f'"{k}" not in {var_name}' for k in keys)
            check = (
                f'{body_indent}if {missing_check}:\n'
                f'{body_indent}    raise KeyError(f"'
                f'\'{var_name}\' is missing required key(s): '
                f'{{[k for k in ({keys!r}) if k not in {var_name}]}}")'
            )
            fix_code = f"{def_line}\n{check}"

            findings.append({
                "category": "runtime",
                "severity": "warning",
                "file": filename,
                "line": target_line,
                "message": (
                    f"'{var_name}' is accessed with key{'s' if len(keys) > 1 else ''} "
                    f"{keys_display} with no existence check — this raises KeyError if "
                    f"the caller doesn't include {'them' if len(keys) > 1 else 'it'}."
                ),
                "bad_code": def_line.strip(),
                "fix_type": "dict_key_guard",
                "fix": fix_code,
                "reason": (
                    f"Consolidated {len(keys)} unguarded key access(es) on parameter "
                    f"'{var_name}' into a single check at the top of the function."
                ),
            })

    return findings
