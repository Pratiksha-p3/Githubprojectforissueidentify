"""
src/analyzers/unstored_constructor_param_checker.py

Detects a constructor parameter that __init__ accepts but never stores on
self, when some other method in the same class later reads self.<that
name> — the object is built without error, but raises AttributeError the
first time the missing attribute is actually used.

This exact bug shape was found during manual review this session (a
Student class with `self.age` read in a method but never assigned in
__init__) and was NOT caught by the general LLM review pass at the time —
it's what motivated writing a dedicated deterministic checker for it
rather than relying on an LLM to notice every time.

Deliberately narrow to keep false positives at zero:
  - Only __init__ — the constructor contract is unambiguous, unlike an
    ordinary method where withholding a param from self can be
    intentional.
  - Only flags when self.<param> is actually read somewhere else in the
    class — a param that's genuinely never stored AND never read back
    doesn't matter to anything downstream.
  - Assignment is judged by attribute name equality, not value — so
    `self.name = name.strip()` still counts as storing `name`.
"""
from __future__ import annotations

import ast

from src.analyzers._ast_utils import line_indent
from src.core.models import ConfidenceTier, Finding, Severity


def _param_names(func: ast.FunctionDef) -> list[str]:
    args = func.args
    names = [a.arg for a in args.args if a.arg != "self"]
    names += [a.arg for a in args.posonlyargs]
    names += [a.arg for a in args.kwonlyargs]
    return names


def _self_stored_attrs(func: ast.FunctionDef) -> set[str]:
    stored: set[str] = set()
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if not isinstance(t, ast.Attribute) or not isinstance(t.value, ast.Name):
                continue
            if t.value.id == "self":
                stored.add(t.attr)
    return stored


def _self_read_attrs_outside(cls: ast.ClassDef, init: ast.FunctionDef) -> set[str]:
    read: set[str] = set()
    for member in cls.body:
        if member is init or not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(member):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            if node.value.id == "self":
                read.add(node.attr)
    return read


def detect_unstored_constructor_params(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        init = next(
            (m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"),
            None,
        )
        if init is None or not init.body:
            continue

        params = _param_names(init)
        if not params:
            continue

        stored = _self_stored_attrs(init)
        read_elsewhere = _self_read_attrs_outside(cls, init)
        missing = [p for p in params if p not in stored and p in read_elsewhere]
        if not missing:
            continue

        anchor_line = init.body[-1].end_lineno or init.body[-1].lineno
        if not (0 < anchor_line <= len(lines)):
            continue
        anchor_text = lines[anchor_line - 1]
        indent = line_indent(anchor_text)

        inserts = "\n".join(f"{indent}self.{p} = {p}" for p in missing)
        fix_code = f"{anchor_text}\n{inserts}"

        if len(missing) == 1:
            p = missing[0]
            message = (
                f"'{p}' is accepted by {cls.name}.__init__ but never stored as "
                f"self.{p} — self.{p} is read elsewhere in {cls.name}, which will "
                f"raise AttributeError."
            )
        else:
            names = ", ".join(f"'{p}'" for p in missing)
            message = (
                f"{names} are accepted by {cls.name}.__init__ but never stored on "
                f"self — each is read as self.<name> elsewhere in {cls.name}, which "
                f"will raise AttributeError."
            )

        findings.append(
            Finding(
                file=filename,
                line=anchor_line,
                category="runtime",
                severity=Severity.CRITICAL,
                message=message,
                bad_code=anchor_text.strip(),
                fix=fix_code,
                confidence=ConfidenceTier.MEDIUM,
                source="unstored_constructor_param_checker",
            )
        )

    return findings
