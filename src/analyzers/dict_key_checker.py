"""
src/analyzers/dict_key_checker.py

Covers two KeyError shapes, deliberately kept separate because their
certainty is completely different -- same split
src/analyzers/index_guard_checker.py makes for IndexError, for the same
reason:

detect_unguarded_dict_access() -- dict[literal_key] access with no
existence guard, on a dict whose exact contents can't be verified
statically: a FUNCTION PARAMETER (the caller could pass anything), or a
local variable assigned from a `<expr>.json()` call (an HTTP response
body's shape isn't knowable at authoring time either -- confirmed
against a real bug: `data = fetch_data(url); data["address"]["city"]`
raised KeyError in practice, and was invisible to this checker until
this second source was added, since `data` is a local, not a
parameter). This is a "might happen depending on what's passed in or
what the response contains" finding (WARNING, MEDIUM confidence, a
suggested guard as the fix). Every unguarded access to the same
variable is consolidated into one finding + fix instead of one
duplicate finding per key access.
  - Only literal string keys (`d["x"]`), not `d[some_variable]` -- a
    dynamic key can't be checked against without more context anyway.
  - Skips a key already guarded by `if "x" in d`, `"x" not in d`, a
    `.get(...)` call visible anywhere on the same dict, or a surrounding
    try/except KeyError.

_missing_key_findings() -- `{"a": 1}["b"]` (or a variable assigned to a
dict literal exactly once, never reassigned): a LITERAL dict indexed
with a literal key that ISN'T one of its own keys. Unlike the
parameter/`.json()` case, there's no "might" here -- the literal's keys
are an AST-verifiable fact, so this is CRITICAL, not WARNING, and
doesn't need any guard-detection at all. Skipped entirely (not "assumed
missing") for any dict literal containing a dynamic key or a `**`
unpacking entry -- the actual key set isn't fully knowable then, and
guessing wrong here would be a false "always fails" claim on a key that
might well be present at runtime. Still no fix: even though the finding
is certain, the correct resolution isn't derivable from the literal
alone, same stance as index_guard_checker's equivalent.

The fix's insertion point for detect_unguarded_dict_access() differs by
source: a parameter exists from the top of the function, so its guard
goes right after the `def` line (as before). A `.json()`-derived local
doesn't exist until its assignment executes -- inserting the guard at
the top of the function would reference the name before it's bound
(NameError/UnboundLocalError instead of the intended guard), so its
guard goes immediately after that assignment statement instead.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import (
    build_parent_map,
    exception_names,
    line_indent,
    owning_function,
    param_names,
)
from src.core.models import ConfidenceTier, Finding, Severity


def _already_guarded(func: ast.AST, var_name: str, key: str) -> bool:
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
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == var_name
            ):
                return True
        if isinstance(node, ast.Try):
            names = [n for h in node.handlers for n in exception_names(h.type)]
            if any(n in ("KeyError", "Exception") for n in names):
                return True
    return False


def _json_returning_function_names(tree: ast.AST) -> set[str]:
    """Same-file functions whose body returns `<expr>.json()` somewhere
    -- e.g. `def fetch_data(url): return requests.get(url).json()`.
    A caller doing `data = fetch_data(url)` is just as exposed to an
    unpredictable response shape as calling `.json()` directly; this is
    what makes the real-world wrapper-function shape (confirmed against
    an actual bug: `data = fetch_data(url); data["address"]["city"]`)
    visible to _json_derived_locals() below, not just the inline case."""
    names: set[str] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "json"
            ):
                names.add(func.name)
                break
    return names


def _json_derived_locals(
    func: ast.AST, json_returning_funcs: set[str]
) -> dict[str, tuple[int, int]]:
    """Maps variable name -> (start_line, end_line) of its assignment,
    for every `x = <expr>.json()` OR `x = some_wrapper(...)` (where
    some_wrapper is a same-file function that itself returns
    `<expr>.json()`) directly in `func`'s body (not a nested function's).
    end_line matters as much as start_line: the assignment can span
    multiple lines (`x = fetch(\\n    url\\n)`), and a guard spliced in
    after just the FIRST line would truncate the call mid-expression --
    confirmed live as a real bug (produced `data = fetch_data(` with no
    closing paren, caught by src/core/grounding.py's is_valid_fix() and
    silently dropped rather than shown broken, but that meant the whole
    finding vanished with no explanation instead of being fixed)."""
    result: dict[str, tuple[int, int]] = {}
    for node in ast.walk(func):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        is_direct_json = isinstance(value.func, ast.Attribute) and value.func.attr == "json"
        is_json_wrapper = (
            isinstance(value.func, ast.Name) and value.func.id in json_returning_funcs
        )
        if not (is_direct_json or is_json_wrapper):
            continue
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        result[target.id] = (node.lineno, end_lineno)
    return result


def _dict_literal_keys(node: ast.Dict) -> set[str] | None:
    """The dict literal's own string keys, or None if any key isn't a
    known literal string -- a dynamic key (`{compute(): 1}`) or a `**`
    unpacking entry (`{**other, "a": 1}`) means the actual key set isn't
    fully knowable, so the whole literal is treated as unverifiable
    rather than guessed at (same "no fix is better than a wrong claim"
    stance as everywhere else in this project)."""
    keys: set[str] = set()
    for key_node in node.keys:
        if key_node is None or not (
            isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
        ):
            return None
        keys.add(key_node.value)
    return keys


def _single_assignment_dict_literal_keys(tree: ast.AST) -> dict[str, set[str]]:
    """Maps variable name -> known keys, for every name assigned to a
    dict literal EXACTLY ONCE anywhere in the file, and never reassigned
    to anything else -- same conservative "no real dataflow analysis"
    scope as src/analyzers/index_guard_checker.py's equivalent (a name
    reassigned anywhere, even in an unrelated function, is dropped
    entirely rather than risk pairing a stale key set with a later,
    differently-sourced use)."""
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        assignments.setdefault(target.id, []).append(node.value)

    result: dict[str, set[str]] = {}
    for name, values in assignments.items():
        if len(values) != 1 or not isinstance(values[0], ast.Dict):
            continue
        keys = _dict_literal_keys(values[0])
        if keys is not None:
            result[name] = keys
    return result


def _missing_key_findings(tree: ast.AST, filename: str, lines: list[str]) -> list[Finding]:
    tracked_vars = _single_assignment_dict_literal_keys(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue

        if isinstance(node.value, ast.Dict):
            known_keys = _dict_literal_keys(node.value)
            described = "This dict literal"
        elif isinstance(node.value, ast.Name) and node.value.id in tracked_vars:
            known_keys = tracked_vars[node.value.id]
            described = f"'{node.value.id}'"
        else:
            continue
        if known_keys is None:
            continue

        key_node = node.slice
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        key = key_node.value
        if key in known_keys:
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="runtime",
                severity=Severity.CRITICAL,
                message=(
                    f"{described} does not have the key '{key}' — guaranteed "
                    f"KeyError every time this line runs, not just a possible one."
                ),
                bad_code=lines[node.lineno - 1].strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="dict_key_checker",
            )
        )
    return findings


def detect_unguarded_dict_access(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    parent_map = build_parent_map(tree)
    json_returning_funcs = _json_returning_function_names(tree)

    findings: list[Finding] = list(_missing_key_findings(tree, filename, lines))
    seen_funcs: set[int] = set()

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(func) in seen_funcs:
            continue
        seen_funcs.add(id(func))

        params = param_names(func)
        json_locals = _json_derived_locals(func, json_returning_funcs)
        risky_vars = params | json_locals.keys()
        if not risky_vars:
            continue

        by_var: dict[str, list[str]] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
                continue
            var_name = node.value.id
            if var_name not in risky_vars:
                continue
            if owning_function(parent_map, node) is not func:
                continue  # belongs to a nested function shadowing the same name

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
            # Parameters are guarded right after `def` (a single line);
            # a .json()-derived local doesn't exist until its assignment
            # finishes executing, so its guard goes right after the
            # assignment's FULL span (which may cover several lines).
            is_param = var_name in params
            if is_param:
                start_line = end_line = func.lineno
            else:
                start_line, end_line = json_locals[var_name]
            if not (0 < start_line <= len(lines) and end_line <= len(lines)):
                continue
            anchor_text = "\n".join(lines[start_line - 1 : end_line])
            first_line = lines[start_line - 1]
            body_indent = line_indent(first_line) + ("    " if is_param else "")

            keys_display = ", ".join(f"'{k}'" for k in keys)
            missing_check = " or ".join(f'"{k}" not in {var_name}' for k in keys)
            check = (
                f'{body_indent}if {missing_check}:\n'
                f'{body_indent}    raise KeyError(f"'
                f"'{var_name}' is missing required key(s): "
                f'{{[k for k in ({keys!r}) if k not in {var_name}]}}")'
            )
            fix_code = f"{anchor_text}\n{check}"

            source_description = (
                "the caller doesn't include them" if is_param
                else "the response doesn't include it"
            )
            findings.append(
                Finding(
                    file=filename,
                    line=start_line,
                    end_line=end_line if end_line > start_line else 0,
                    category="runtime",
                    severity=Severity.WARNING,
                    message=(
                        f"'{var_name}' is accessed with key"
                        f"{'s' if len(keys) > 1 else ''} {keys_display} with no "
                        f"existence check — raises KeyError if {source_description}."
                    ),
                    bad_code=first_line.strip(),
                    fix=fix_code,
                    confidence=ConfidenceTier.MEDIUM,
                    source="dict_key_checker",
                )
            )

    return findings
