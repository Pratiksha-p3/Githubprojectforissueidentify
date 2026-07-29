"""
src/analyzers/_ast_utils.py

Small AST helpers shared across the checkers in this package — kept in one
place so five checkers don't each re-derive parent-tracking or parameter
extraction slightly differently.
"""
from __future__ import annotations

import ast


def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Maps id(child) -> parent node. ast doesn't track parents natively."""
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def owning_function(
    parent_map: dict[int, ast.AST], node: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = parent_map.get(id(node))
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parent_map.get(id(current))
    return None


def param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = func.args
    names = {a.arg for a in args.args}
    names |= {a.arg for a in args.posonlyargs}
    names |= {a.arg for a in args.kwonlyargs}
    return names


def exception_names(handler_type: ast.expr | None) -> list[str]:
    """Handler exception name(s) from `except <type>:` — handles bare
    (KeyError), dotted (pkg.errors.KeyError), and tuple forms."""
    if handler_type is None:
        return []
    if isinstance(handler_type, ast.Name):
        return [handler_type.id]
    if isinstance(handler_type, ast.Attribute):
        return [handler_type.attr]
    if isinstance(handler_type, ast.Tuple):
        names: list[str] = []
        for elt in handler_type.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
            elif isinstance(elt, ast.Attribute):
                names.append(elt.attr)
        return names
    return []


def line_indent(line: str) -> str:
    return " " * (len(line) - len(line.lstrip()))
