"""
vectordb/pinecone_store.py

Pinecone alternative to ChromaStore — implements the exact same public
interface (upsert, query, delete_by_filename, count, reset) so it's a
drop-in swap via vectordb.factory.get_vector_store(). The app runs fully
on the free local ChromaDB by default; set VECTOR_DB_PROVIDER=pinecone
and PINECONE_API_KEY to switch the whole RAG pipeline over.

Pinecone has no separate "documents" store like Chroma does — chunk
content is kept in vector metadata instead (under Pinecone's ~40KB
per-vector metadata cap, hence the truncation below).

Install: pip install pinecone
"""
from __future__ import annotations

from config import cfg
from embeddings.embed import EmbeddedChunk
from ingestion.chunker import CodeChunk
from vectordb.chroma_store import RetrievedChunk

_MAX_METADATA_CONTENT = 8000  # keep well under Pinecone's per-vector metadata cap


class PineconeStore:

    def __init__(self):
        self._index = None

    def _get_index(self):
        if self._index is not None:
            return self._index

        from pinecone import Pinecone, ServerlessSpec

        pc = Pinecone(api_key=cfg.pinecone_api_key)
        existing = [i["name"] for i in pc.list_indexes()]
        if cfg.pinecone_index not in existing:
            print(f"[pinecone] Creating index '{cfg.pinecone_index}' "
                  f"(dim={cfg.pinecone_dimension})")
            pc.create_index(
                name=cfg.pinecone_index,
                dimension=cfg.pinecone_dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud=cfg.pinecone_cloud, region=cfg.pinecone_region),
            )

        self._index = pc.Index(cfg.pinecone_index)
        print(f"[pinecone] Index '{cfg.pinecone_index}' "
              f"({self.count()} vectors in namespace '{cfg.pinecone_namespace}')")
        return self._index

    def upsert(self, embedded_chunks: list[EmbeddedChunk]) -> None:
        if not embedded_chunks:
            return

        index = self._get_index()

        # Same dedup as ChromaStore.upsert — chunk_id can collide when the
        # parser produces structurally identical sections; keep the last
        # occurrence, matching upsert-overwrite semantics.
        seen_ids = set()
        deduped = []
        for ec in reversed(embedded_chunks):
            if ec.chunk.chunk_id in seen_ids:
                continue
            seen_ids.add(ec.chunk.chunk_id)
            deduped.append(ec)
        deduped.reverse()

        vectors = []
        for ec in deduped:
            c = ec.chunk
            vectors.append({
                "id": c.chunk_id,
                "values": ec.vector,
                "metadata": {
                    "filename": c.filename,
                    "language": c.language,
                    "section_name": c.section_name,
                    "section_type": c.section_type,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "chunk_index": c.chunk_index,
                    "token_estimate": c.token_estimate,
                    "content": c.content[:_MAX_METADATA_CONTENT],
                },
            })

        index.upsert(vectors=vectors, namespace=cfg.pinecone_namespace)
        print(f"[pinecone] Upserted {len(vectors)} chunks (total={self.count()})")

    def query(
        self,
        query_vector: list[float],
        top_k: int = None,
        language_filter: str = None,
        filename_filter: str = None,
    ) -> list[RetrievedChunk]:
        if top_k is None:
            top_k = cfg.top_k

        index = self._get_index()
        if self.count() == 0:
            print("[pinecone] Empty index")
            return []

        filter_ = {}
        if language_filter:
            filter_["language"] = {"$eq": language_filter}
        if filename_filter:
            filter_["filename"] = {"$eq": filename_filter}

        result = index.query(
            vector=query_vector,
            top_k=top_k,
            namespace=cfg.pinecone_namespace,
            filter=filter_ or None,
            include_metadata=True,
        )

        retrieved = []
        for match in result.get("matches", []):
            meta = match.get("metadata", {}) or {}
            score = max(0.0, min(1.0, match.get("score", 0.0)))
            chunk = CodeChunk(
                chunk_id=match["id"],
                filename=meta.get("filename", "unknown"),
                language=meta.get("language", "unknown"),
                section_name=meta.get("section_name", "unknown"),
                section_type=meta.get("section_type", "module"),
                content=meta.get("content", ""),
                start_line=int(meta.get("start_line", 1)),
                end_line=int(meta.get("end_line", 1)),
                chunk_index=int(meta.get("chunk_index", 0)),
                token_estimate=int(meta.get("token_estimate", 0)),
            )
            retrieved.append(RetrievedChunk(chunk=chunk, score=score, distance=1.0 - score))

        retrieved.sort(key=lambda x: x.score, reverse=True)
        return retrieved

    def delete_by_filename(self, filename: str) -> None:
        index = self._get_index()
        index.delete(filter={"filename": {"$eq": filename}}, namespace=cfg.pinecone_namespace)
        print(f"[pinecone] Deleted {filename}")

    def count(self) -> int:
        index = self._index
        if index is None:
            # avoid infinite recursion via _get_index() -> count() on first call
            from pinecone import Pinecone
            pc = Pinecone(api_key=cfg.pinecone_api_key)
            if cfg.pinecone_index not in [i["name"] for i in pc.list_indexes()]:
                return 0
            index = pc.Index(cfg.pinecone_index)
        stats = index.describe_index_stats()
        ns_stats = (stats.get("namespaces") or {}).get(cfg.pinecone_namespace, {})
        return ns_stats.get("vector_count", 0)

    def reset(self) -> None:
        try:
            index = self._get_index()
            index.delete(delete_all=True, namespace=cfg.pinecone_namespace)
            print("[pinecone] Reset complete")
        except Exception as e:
            print(f"[pinecone] Reset failed: {e}")
