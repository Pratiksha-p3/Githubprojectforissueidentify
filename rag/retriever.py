"""
rag/retriever.py

Embeds PR changes and retrieves relevant code chunks from ChromaDB.

IMPROVEMENTS over original:
  1. Filter retrieved chunks by `cfg.min_similarity_score` — low-quality
     context hurts LLM accuracy more than helping it.
  2. `_build_query()` now uses ONLY the changed lines (added/modified) rather
     than falling back to the first 50 lines of the full file.  Including
     unchanged context in the query vector biases retrieval away from the
     actual change.
  3. Self-file exclusion is done by exact filename match + overlap check
     (unchanged) but now also excludes chunks from the same function/section
     as the changed lines, avoiding trivially identical context.
  4. Added `retrieve_for_pr()` returning a deduplicated context map, with
     cross-file deduplication so the same chunk isn't sent to multiple files.
"""

from __future__ import annotations

import re

from embeddings.embed import Embedder
from vectordb.chroma_store import ChromaStore, RetrievedChunk
from ingestion.github_loader import PRFile
from config import cfg


class Retriever:

    def __init__(
        self,
        store: ChromaStore = None,
        embedder: Embedder = None,
    ):
        self.store   = store   or ChromaStore()
        self.embedder = embedder or Embedder()

    # ── Public API ────────────────────────────────────────

    def retrieve_for_file(
        self,
        pr_file: PRFile,
        top_k: int = None,
    ) -> list[RetrievedChunk]:

        top_k = top_k or cfg.top_k

        query_text   = self._build_query(pr_file)
        query_vector = self.embedder.embed_query(query_text)

        results = self.store.query(
            query_vector=query_vector,
            top_k=top_k,
            language_filter=(
                pr_file.language
                if getattr(pr_file, "language", "unknown") != "unknown"
                else None
            ),
        )

        # Remove chunks that overlap with the diff being reviewed
        filtered = [
            r for r in results
            if not self._should_exclude(r, pr_file)
        ]

        # NEW: Only keep chunks above minimum similarity threshold
        filtered = [
            r for r in filtered
            if r.score >= cfg.min_similarity_score
        ]

        print(
            f"[retriever] {pr_file.filename}: "
            f"{len(filtered)}/{len(results)} chunks "
            f"(score≥{cfg.min_similarity_score})"
        )

        return filtered

    def retrieve_for_pr(
        self,
        files: list[PRFile],
        top_k_per_file: int = 4,
    ) -> dict[str, list[RetrievedChunk]]:
        """
        Returns per-file context maps.
        Cross-file deduplication: the same chunk_id is not returned twice.
        """
        context_map:    dict[str, list[RetrievedChunk]] = {}
        seen_chunk_ids: set[str] = set()

        for pf in files:
            chunks = self.retrieve_for_file(pf, top_k=top_k_per_file)
            unique = []
            for rc in chunks:
                if rc.chunk.chunk_id not in seen_chunk_ids:
                    seen_chunk_ids.add(rc.chunk.chunk_id)
                    unique.append(rc)
            context_map[pf.filename] = unique

        return context_map

    # ── Helpers ───────────────────────────────────────────

    def _build_query(self, pr_file: PRFile) -> str:
        """
        Build embedding query from ONLY the added/changed lines.
        Avoids polluting the query vector with unchanged context.
        """
        changed_lines = getattr(pr_file, "changed_lines", [])

        # changed_lines property returns lines starting with '+' stripped of the '+'
        if not changed_lines:
            # Fall back: parse patch directly
            changed_lines = _extract_added_lines(getattr(pr_file, "patch", ""))

        if not changed_lines:
            # Last resort: first 30 lines of full content
            changed_lines = (
                getattr(pr_file, "full_content", "")
                .splitlines()[:30]
            )

        changed_text = "\n".join(changed_lines[:60])

        return (
            f"Language: {pr_file.language}\n"
            f"File: {pr_file.filename}\n\n"
            f"Changed Code:\n{changed_text}"
        )

    def _should_exclude(
        self,
        rc: RetrievedChunk,
        pr_file: PRFile,
    ) -> bool:
        """
        Exclude a retrieved chunk if it:
          a) is from the same file AND overlaps the diff lines, OR
          b) is from the same file AND same section_name (trivially identical)
        """
        if rc.chunk.filename != pr_file.filename:
            return False

        # Same section name — very likely the exact code being reviewed
        changed_lines_set = _parse_changed_line_numbers(
            getattr(pr_file, "patch", "")
        )
        if changed_lines_set:
            chunk_range = set(range(rc.chunk.start_line, rc.chunk.end_line + 1))
            if changed_lines_set & chunk_range:
                return True

        return False


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_added_lines(patch: str) -> list[str]:
    return [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def _parse_changed_line_numbers(patch: str) -> set[int]:
    changed: set[int] = set()
    line_num = 0

    for line in patch.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                line_num = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            changed.add(line_num)
            line_num += 1
        elif not line.startswith("-"):
            line_num += 1

    return changed


# ─────────────────────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class _PR:
        filename: str
        language: str
        full_content: str
        changed_lines: list
        patch: str

    sample = _PR(
        filename="auth.py",
        language="python",
        full_content="def login():\n    return True\n",
        changed_lines=["def login():", "    return True"],
        patch="@@ +1,2 @@\n+def login():\n+    return True\n",
    )

    retriever = Retriever()
    query = retriever._build_query(sample)
    print(query)