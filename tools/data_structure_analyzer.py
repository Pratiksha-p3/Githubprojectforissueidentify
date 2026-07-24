"""
tools/data_structure_analyzer.py

Heuristic "data structure complexity" scoring. There's no single
industry-standard metric for this (unlike cyclomatic complexity), so
this measures three concrete, inspectable things instead:

  1. Container nesting depth — a dict of lists of dicts is harder to
     reason about and mutate safely than a flat list.
  2. Variety of built-in container types used in one file (list/dict/
     set/tuple) — more variety usually means more conversion/adapter
     code and more ways to get types wrong.
  3. Custom recursive structures — classes with self-referential
     attributes (.next/.prev/.left/.right/.parent/.children/...),
     i.e. hand-rolled linked lists/trees/graphs. These carry real
     maintainability and performance implications (traversal
     correctness, cycle risk, no built-in bounds checking) that plain
     dict/list usage doesn't.
"""
from __future__ import annotations

import ast

_RECURSIVE_ATTR_HINTS = {
    "next", "prev", "left", "right", "parent",
    "children", "child", "head", "tail",
}


class DataStructureAnalyzer:

    def analyze(self, code: str, filename: str) -> dict:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return {
                "filename": filename,
                "max_nesting_depth": 0,
                "container_types_used": [],
                "custom_structures": [],
                "rating": "unknown",
            }

        max_depth = 0
        types_used = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
                max_depth = max(max_depth, self._container_depth(node))
                types_used.add(type(node).__name__.lower())

        custom_structures = self._find_custom_structures(tree)

        return {
            "filename": filename,
            "max_nesting_depth": max_depth,
            "container_types_used": sorted(types_used),
            "custom_structures": custom_structures,
            "rating": self._rate(max_depth, len(types_used), len(custom_structures)),
        }

    def analyze_files(self, files: list) -> list[dict]:
        results = []
        for pf in files:
            if getattr(pf, "language", "") != "python":
                continue
            content = getattr(pf, "full_content", "") or ""
            if not content.strip():
                continue
            results.append(self.analyze(content, pf.filename))
        return results

    def summarize(self, results: list[dict]) -> dict:
        if not results:
            return {"overall_rating": "n/a", "max_nesting_depth": 0,
                     "files_with_custom_structures": 0, "per_file": []}

        rank = {"unknown": 0, "low": 1, "moderate": 2, "high": 3}
        worst = max(results, key=lambda r: rank.get(r["rating"], 0))
        return {
            "overall_rating": worst["rating"],
            "max_nesting_depth": max(r["max_nesting_depth"] for r in results),
            "files_with_custom_structures": sum(1 for r in results if r["custom_structures"]),
            "per_file": results,
        }

    # ── Internal ──────────────────────────────────────────

    def _container_depth(self, node, current: int = 1) -> int:
        if isinstance(node, ast.Dict):
            children = node.values
        elif isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            children = node.elts
        else:
            children = []

        deepest = current
        for child in children:
            if isinstance(child, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
                deepest = max(deepest, self._container_depth(child, current + 1))
        return deepest

    def _find_custom_structures(self, tree) -> list[dict]:
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            attrs = set()
            for n in ast.walk(node):
                if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                        and n.value.id == "self"):
                    attrs.add(n.attr.lower())
            hints = attrs & _RECURSIVE_ATTR_HINTS
            if hints:
                found.append({
                    "class": node.name,
                    "line": node.lineno,
                    "hint_attrs": sorted(hints),
                })
        return found

    def _rate(self, max_depth: int, type_variety: int, custom_count: int) -> str:
        score = 0
        if max_depth >= 4:
            score += 2
        elif max_depth >= 3:
            score += 1
        if type_variety >= 3:
            score += 1
        score += custom_count  # each custom recursive structure is real added complexity

        if score >= 3:
            return "high"
        if score >= 1:
            return "moderate"
        return "low"
