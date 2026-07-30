"""
src/cli/index_repo.py

`review-cli index <directory>` — walks a local directory, chunks every
Python file (AST-aware, src/ingestion/chunker.py), embeds each chunk
(src/embeddings/embed.py), and upserts into the vector store
(src/vectordb/chroma_store.py) that src/rag/retriever.py queries later.

Operates on a local directory rather than fetching from the GitHub/GitLab
API — cloning/fetching a remote repo is a separate ingestion concern, not
indexing itself.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.embeddings.embed import embed_texts
from src.ingestion.chunker import chunk_file
from src.vectordb.chroma_store import ChromaStore

_SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "node_modules", "dist", "build", ".cache"}


def index_directory(directory: str, *, store: ChromaStore | None = None) -> int:
    root = Path(directory)
    if not root.exists():
        print(f"Directory not found: {directory}")
        return 1

    store = store or ChromaStore()
    total_files = 0
    total_chunks = 0

    for path in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue

        content = path.read_text(encoding="utf-8", errors="replace")
        rel_path = str(path.relative_to(root))
        chunks = chunk_file(rel_path, content)
        if not chunks:
            continue

        store.delete_file(rel_path)  # replace, not accumulate stale chunks from a prior version
        embeddings = embed_texts([c.content for c in chunks])
        store.upsert(chunks, embeddings)

        total_files += 1
        total_chunks += len(chunks)
        print(f"  {rel_path} -> {len(chunks)} chunk(s)")

    print(f"\nIndexed {total_files} file(s), {total_chunks} chunk(s) total. "
          f"Vector store: {store.count()} vectors.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review-cli index")
    parser.add_argument("directory", help="Path to the directory to index")
    args = parser.parse_args(argv)
    return index_directory(args.directory)


if __name__ == "__main__":
    sys.exit(main())
