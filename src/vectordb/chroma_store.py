"""
src/vectordb/chroma_store.py

Thin wrapper around ChromaDB so the vector-store backend is swappable
later without touching src/rag/retriever.py or the indexing CLI —
callers only ever see upsert()/query()/delete_file()/count(), never the
chromadb client API directly.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import chromadb

from src.core.config import settings
from src.ingestion.chunker import CodeChunk


@dataclass
class RetrievedChunk:
    chunk: CodeChunk
    score: float


class ChromaStore:
    def __init__(self, collection_name: str = "code_review", persist_dir: str | None = None):
        client = chromadb.PersistentClient(path=persist_dir or settings.chroma_persist_dir)
        self._collection = client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    def upsert(self, chunks: list[CodeChunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        self._collection.upsert(
            ids=[self._chunk_id(c) for c in chunks],
            embeddings=embeddings,  # type: ignore[arg-type]
            documents=[c.content for c in chunks],
            metadatas=[
                {
                    "filename": c.filename,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "chunk_type": c.chunk_type,
                    "name": c.name,
                }
                for c in chunks
            ],
        )

    def query(self, query_vector: list[float], top_k: int = 5) -> list[RetrievedChunk]:
        if self._collection.count() == 0:
            return []
        results = self._collection.query(
            query_embeddings=[query_vector],  # type: ignore[arg-type]
            n_results=min(top_k, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        # We always pass an explicit include=[...] above, so these fields
        # are guaranteed present — chromadb's return type is just more
        # defensive (Optional) than our actual usage ever hits.
        ids = results["ids"]
        metadatas = results["metadatas"]
        documents = results["documents"]
        distances = results["distances"]
        assert ids is not None and metadatas is not None
        assert documents is not None and distances is not None

        retrieved: list[RetrievedChunk] = []
        for i in range(len(ids[0])):
            meta = metadatas[0][i]
            score = max(0.0, 1.0 - distances[0][i])
            chunk = CodeChunk(
                filename=str(meta.get("filename", "")),
                content=documents[0][i],
                start_line=int(meta.get("start_line", 0)),  # type: ignore[arg-type]
                end_line=int(meta.get("end_line", 0)),  # type: ignore[arg-type]
                chunk_type=str(meta.get("chunk_type", "")),
                name=str(meta.get("name", "")),
            )
            retrieved.append(RetrievedChunk(chunk=chunk, score=score))
        return retrieved

    def delete_file(self, filename: str) -> None:
        try:
            self._collection.delete(where={"filename": {"$eq": filename}})  # type: ignore[dict-item]
        except Exception:
            pass

    def count(self) -> int:
        return self._collection.count()

    @staticmethod
    def _chunk_id(chunk: CodeChunk) -> str:
        raw = f"{chunk.filename}:{chunk.start_line}:{chunk.end_line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]
