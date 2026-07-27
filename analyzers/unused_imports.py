# analyzers/unused_imports.py
"""
Detects imports that are never referenced anywhere in the file.

The old "unused import" check (tools/python_analyzer.py, now dead code —
nothing imports that module) only handled `import x` and always reported
line 1 regardless of where the import actually was. This one:

  - handles both `import x` and `from x import y` (including `as` aliases
    and dotted `import a.b.c`, which binds the name `a`, not `a.b.c`)
  - reports the import's real line number
  - treats a name listed in a module-level `__all__` as used (a common,
    intentional re-export pattern — not "just sits there unused")
  - skips `from x import *` entirely rather than guessing, since a star
    import's names can't be resolved without executing the module
  - flags (but does not offer a one-click removal for) a name imported
    on a line that imports other names too ("import os, sys" or
    "from x import a, b") — removing just one name safely means editing
    the line, not deleting it, so those are informational only
"""
from __future__ import annotations

import ast

# A single-line-replacement "fix" that means "delete this line" rather
# than "replace it with this text" — GitHub natively supports an empty
# ```suggestion``` block as a one-click line deletion. Recognized
# specially by agents/autofix_engine.py's validation/rendering and by
# ingestion/github_loader.py's comment body builder.
DELETE_LINE_SENTINEL = "<<<REMOVE_LINE>>>"


def _bound_names(node) -> list[str]:
    """Names a single Import/ImportFrom node binds into the namespace."""
    if isinstance(node, ast.Import):
        return [a.asname or a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom):
        return [a.asname or a.name for a in node.names if a.name != "*"]
    return []


def _all_exported_names(tree) -> set[str]:
    """Names listed in a module-level `__all__ = [...]`/`(...)` assignment."""
    exported = set()
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    exported.add(elt.value)
    return exported


def detect_unused_imports(code: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    lines = code.splitlines()
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= _all_exported_names(tree)

    import_nodes = [
        n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
    ]

    # One finding per import *line*, not per unused name — a downstream
    # dedup step keeps only one finding per (file, line), so multiple
    # findings on the same line would silently collapse to one and drop
    # the rest of the names from the report.
    findings = []
    for node in import_nodes:
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            continue  # can't determine usage of a star-import's names

        names = _bound_names(node)
        unused = [n for n in names if n not in used]
        if not unused:
            continue

        lineno = node.lineno
        if not (0 < lineno <= len(lines)):
            continue
        line_text = lines[lineno - 1]

        # Safe to auto-remove the whole line only when every name it
        # binds is unused — a line importing two names where only one
        # is unused needs that one name edited out, not the line deleted.
        whole_line_unused = len(unused) == len(names)
        names_display = ", ".join(f"'{n}'" for n in unused)
        plural = len(unused) > 1

        findings.append({
            "file": filename,
            "line": lineno,
            "severity": "warning",
            "category": "quality",
            "message": (
                f"Unused import{'s' if plural else ''}: {names_display} "
                f"{'are' if plural else 'is'} imported but never used."
            ),
            "bad_code": line_text.strip(),
            "fix": DELETE_LINE_SENTINEL if whole_line_unused else "",
            "reason": (
                "Safe to remove — nothing in the file references "
                f"{'these names' if plural else 'this name'}."
                if whole_line_unused else
                "This line imports more than one name and not all of them are "
                "unused — remove just the unused name(s) manually rather than "
                "the whole line."
            ),
        })

    return findings
