"""
src/ingestion/chunker.py

AST-aware chunking: splits a Python file into chunks at function/class
boundaries rather than fixed-size line windows, so each chunk retrieved
by RAG (src/rag/retriever.py) is a complete, coherent unit — a whole
function or class — instead of an arbitrary slice that might cut one in
half and retrieve half a function as "similar existing code".

Falls back to fixed-size line-window chunking for anything that doesn't
parse as valid Python (non-Python files, or a Python file with a syntax
error) — a real repo has both, and neither should make indexing crash.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

_WINDOW_LINES = 60
_WINDOW_OVERLAP = 10


@dataclass
class CodeChunk:
    filename: str
    content: str
    start_line: int
    end_line: int
    chunk_type: str  # "function" | "class" | "module" | "window"
    name: str = ""


def chunk_file(filename: str, content: str) -> list[CodeChunk]:
    if not content.strip():
        return []

    if filename.endswith(".py"):
        try:
            return _chunk_python_ast(filename, content)
        except SyntaxError:
            pass  # not valid Python right now — fall through to window chunking

    return _chunk_by_window(filename, content)


def _chunk_python_ast(filename: str, content: str) -> list[CodeChunk]:
    tree = ast.parse(content)
    lines = content.splitlines()

    chunks: list[CodeChunk] = []
    covered: set[int] = set()

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = node.end_lineno or start
        chunks.append(
            CodeChunk(
                filename=filename,
                content="\n".join(lines[start - 1 : end]),
                start_line=start,
                end_line=end,
                chunk_type="class" if isinstance(node, ast.ClassDef) else "function",
                name=node.name,
            )
        )
        covered.update(range(start, end + 1))

    # Anything at module level not covered by a function/class chunk
    # (imports, module-level constants, top-level script code) becomes
    # its own chunk too, so nothing in the file is silently dropped.
    for start, end in _uncovered_ranges(len(lines), covered):
        text = "\n".join(lines[start - 1 : end]).strip()
        if not text:
            continue
        chunks.append(
            CodeChunk(
                filename=filename, content=text, start_line=start, end_line=end,
                chunk_type="module",
            )
        )

    return sorted(chunks, key=lambda c: c.start_line)


def _uncovered_ranges(total_lines: int, covered: set[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for line in range(1, total_lines + 1):
        if line not in covered:
            if start is None:
                start = line
        elif start is not None:
            ranges.append((start, line - 1))
            start = None
    if start is not None:
        ranges.append((start, total_lines))
    return ranges


def _chunk_by_window(filename: str, content: str) -> list[CodeChunk]:
    lines = content.splitlines()
    step = max(1, _WINDOW_LINES - _WINDOW_OVERLAP)
    chunks: list[CodeChunk] = []
    for i in range(0, len(lines), step):
        batch = lines[i : i + _WINDOW_LINES]
        text = "\n".join(batch).strip()
        if not text:
            continue
        chunks.append(
            CodeChunk(
                filename=filename, content=text,
                start_line=i + 1, end_line=i + len(batch), chunk_type="window",
            )
        )
    return chunks
