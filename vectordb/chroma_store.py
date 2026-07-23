"""
vectordb/chroma_store.py

Stores and queries code embeddings using ChromaDB.

Data Flow:

Upsert:
    list[EmbeddedChunk]
            ↓
        ChromaDB

Query:
    query_vector
            ↓
    list[RetrievedChunk]
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass

# Disable ChromaDB's built-in telemetry/opentelemetry integration.
# This avoids version conflicts between chromadb's and semgrep's
# opentelemetry dependencies — telemetry isn't needed for local use anyway.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")
os.environ.setdefault("POSTHOG_DISABLED", "True")

# Hard-disable posthog capture() to silence the
# "capture() takes 1 positional argument but 3 were given" noise —
# this is a posthog/chromadb version mismatch, harmless but noisy.
try:
    import posthog
    posthog.disabled = True
    posthog.capture = lambda *args, **kwargs: None
except ImportError:
    pass

from embeddings.embed import EmbeddedChunk
from ingestion.chunker import CodeChunk
from config import cfg


@dataclass
class RetrievedChunk:
    chunk: CodeChunk
    score: float
    distance: float

    def __repr__(self):
        return (
            f"RetrievedChunk("
            f"{self.chunk.filename} "
            f"{self.chunk.section_name} "
            f"score={self.score:.3f})"
        )


class ChromaStore:

    def __init__(self):
        self._collection = None

    def _get_collection(self):

        if self._collection is not None:
            return self._collection

        try:

            import chromadb

            client = chromadb.PersistentClient(
                path=cfg.chroma_dir
            )

            self._collection = (
                client.get_or_create_collection(
                    name=cfg.chroma_collection,
                    metadata={
                        "hnsw:space": "cosine"
                    },
                )
            )

            print(
                f"[chroma] Collection "
                f"'{cfg.chroma_collection}' "
                f"({self._collection.count()} vectors)"
            )

        except ImportError:

            print(
                "[chroma] chromadb not installed. "
                "Using in-memory fallback."
            )

            self._collection = (
                InMemoryCollection()
            )

        except Exception as e:

            print(
                f"[chroma] chromadb failed to initialise: "
                f"{type(e).__name__}: {e}"
            )
            print(
                "[chroma] Falling back to in-memory store. "
                "This means data won't persist across runs."
            )

            self._collection = (
                InMemoryCollection()
            )

        return self._collection

    def upsert(
        self,
        embedded_chunks: list[EmbeddedChunk],
    ) -> None:

        if not embedded_chunks:
            return

        col = self._get_collection()

        # chunk_id is a hash of (filename, section name, chunk index,
        # content) — two chunks can legitimately land on the same id when
        # a file has structurally identical sections (e.g. the parser
        # doesn't disambiguate same-named functions/classes). ChromaDB's
        # upsert() is supposed to just overwrite on a duplicate id, but it
        # rejects duplicate ids within a single call outright — so dedupe
        # here, keeping the last occurrence (matches upsert-overwrite
        # semantics), before ever calling into the client.
        seen_ids = set()
        deduped = []
        for ec in reversed(embedded_chunks):
            if ec.chunk.chunk_id in seen_ids:
                continue
            seen_ids.add(ec.chunk.chunk_id)
            deduped.append(ec)
        deduped.reverse()

        skipped = len(embedded_chunks) - len(deduped)
        if skipped:
            print(f"[chroma] Skipped {skipped} duplicate-id chunk(s) in this batch")

        ids = []
        embeddings = []
        documents = []
        metadatas = []

        for ec in deduped:

            c = ec.chunk

            ids.append(c.chunk_id)

            embeddings.append(ec.vector)

            documents.append(c.content)

            metadatas.append(
                {
                    "filename": c.filename,
                    "language": c.language,
                    "section_name": c.section_name,
                    "section_type": c.section_type,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "chunk_index": c.chunk_index,
                    "token_estimate": c.token_estimate,
                }
            )

        col.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        print(
            f"[chroma] Upserted "
            f"{len(ids)} chunks "
            f"(total={col.count()})"
        )

    def query(
        self,
        query_vector: list[float],
        top_k: int = None,
        language_filter: str = None,
        filename_filter: str = None,
    ) -> list[RetrievedChunk]:

        if top_k is None:
            top_k = cfg.top_k

        col = self._get_collection()

        if col.count() == 0:

            print(
                "[chroma] Empty collection"
            )

            return []

        where = {}

        if language_filter:
            where["language"] = {
                "$eq": language_filter
            }

        if filename_filter:
            where["filename"] = {
                "$eq": filename_filter
            }

        results = col.query(
            query_embeddings=[query_vector],
            n_results=min(
                top_k,
                col.count(),
            ),
            where=where if where else None,
            include=[
                "documents",
                "metadatas",
                "distances",
            ],
        )

        retrieved = []

        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        for i in range(len(ids)):

            meta = metas[i]

            distance = distances[i]

            score = max(
                0.0,
                min(
                    1.0,
                    1.0 - distance,
                ),
            )

            chunk = CodeChunk(
                chunk_id=ids[i],
                filename=meta.get(
                    "filename",
                    "unknown",
                ),
                language=meta.get(
                    "language",
                    "unknown",
                ),
                section_name=meta.get(
                    "section_name",
                    "unknown",
                ),
                section_type=meta.get(
                    "section_type",
                    "module",
                ),
                content=docs[i],
                start_line=int(
                    meta.get(
                        "start_line",
                        1,
                    )
                ),
                end_line=int(
                    meta.get(
                        "end_line",
                        1,
                    )
                ),
                chunk_index=int(
                    meta.get(
                        "chunk_index",
                        0,
                    )
                ),
                token_estimate=int(
                    meta.get(
                        "token_estimate",
                        0,
                    )
                ),
            )

            retrieved.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=score,
                    distance=distance,
                )
            )

        retrieved.sort(
            key=lambda x: x.score,
            reverse=True,
        )

        return retrieved

    def delete_by_filename(
        self,
        filename: str,
    ):

        col = self._get_collection()

        col.delete(
            where={
                "filename": {
                    "$eq": filename
                }
            }
        )

        print(
            f"[chroma] Deleted "
            f"{filename}"
        )

    def count(self):

        return (
            self._get_collection()
            .count()
        )

    def reset(self):

        try:

            import chromadb

            client = (
                chromadb.PersistentClient(
                    path=cfg.chroma_dir
                )
            )

            try:
                client.delete_collection(
                    cfg.chroma_collection
                )
            except Exception:
                pass

            self._collection = None

            print(
                "[chroma] Reset complete"
            )

        except Exception as e:

            print(
                f"[chroma] Reset failed: {e}"
            )


# =====================================================
# In-Memory Fallback
# =====================================================

class InMemoryCollection:

    def __init__(self):
        self._store = {}

    def count(self):
        return len(self._store)

    def upsert(
        self,
        ids,
        embeddings,
        documents,
        metadatas,
    ):

        for i in range(len(ids)):

            self._store[ids[i]] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }

    def query(
        self,
        query_embeddings,
        n_results,
        where=None,
        include=None,
    ):

        q = query_embeddings[0]

        scored = []

        for id_, item in self._store.items():

            if where:

                skip = False

                for k, v in where.items():

                    if (
                        item["metadata"].get(k)
                        != v["$eq"]
                    ):
                        skip = True
                        break

                if skip:
                    continue

            distance = (
                1.0
                - self._cosine(
                    q,
                    item["embedding"],
                )
            )

            scored.append(
                (
                    id_,
                    item,
                    distance,
                )
            )

        scored.sort(
            key=lambda x: x[2]
        )

        top = scored[:n_results]

        return {
            "ids": [[x[0] for x in top]],
            "documents": [[x[1]["document"] for x in top]],
            "metadatas": [[x[1]["metadata"] for x in top]],
            "distances": [[x[2] for x in top]],
        }

    def delete(self, where):

        remove_ids = []

        for id_, item in self._store.items():

            for k, v in where.items():

                if (
                    item["metadata"].get(k)
                    == v["$eq"]
                ):
                    remove_ids.append(id_)

        for rid in remove_ids:
            del self._store[rid]

    @staticmethod
    def _cosine(a, b):

        dot = sum(
            x * y
            for x, y in zip(a, b)
        )

        ma = math.sqrt(
            sum(x * x for x in a)
        )

        mb = math.sqrt(
            sum(x * x for x in b)
        )

        if ma == 0 or mb == 0:
            return 0.0

        return dot / (ma * mb)


# =====================================================
# Test
# =====================================================

if __name__ == "__main__":

    from embeddings.embed import EmbeddedChunk

    store = ChromaStore()

    chunk = CodeChunk(
        chunk_id="1",
        filename="auth.py",
        language="python",
        section_name="login",
        section_type="function",
        content="def login(): pass",
        start_line=1,
        end_line=2,
        chunk_index=0,
        token_estimate=10,
    )

    embedded = EmbeddedChunk(
        chunk=chunk,
        vector=[0.1] * 1536,
    )

    store.upsert([embedded])

    results = store.query(
        [0.1] * 1536,
        top_k=5,
    )

    print(results)