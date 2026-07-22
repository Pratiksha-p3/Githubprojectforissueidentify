"""
embeddings/embed.py
FREE local embeddings using sentence-transformers.
No API key needed. Model downloads once (~90MB).
Install: pip install sentence-transformers
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from ingestion.chunker import CodeChunk

_MAX_CHARS = 32_000
_DIM = 384  # all-MiniLM-L6-v2 dimension


@dataclass
class EmbeddedChunk:
    chunk: CodeChunk
    vector: list[float]

    def __repr__(self):
        return (
            f"EmbeddedChunk("
            f"{self.chunk.filename} "
            f"{self.chunk.section_name}"
            f"[{self.chunk.chunk_index}])"
        )


class Embedder:
    """
    Free local embeddings via sentence-transformers.
    Downloads model once, then runs fully offline.
    """

    def __init__(self):
        self._model = None
        self._dim   = _DIM
        self._ready = False

    def _get_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
            print("[embed] Loading sentence-transformers model (downloads once ~90MB)...")
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            self._ready = True
            print("[embed] Model loaded! Free local embeddings active.")
        except ImportError:
            print("[embed] ERROR: sentence-transformers not installed!")
            print("[embed] Fix: pip install sentence-transformers")
            print("[embed] Using mock vectors for now (RAG won't work properly).")
            self._model = "mock"
        return self._model

    def embed_chunks(
        self,
        chunks: list[CodeChunk],
        batch_size: int = 32,
    ) -> list[EmbeddedChunk]:

        chunks = [c for c in chunks if c.content.strip()]
        if not chunks:
            return []

        model = self._get_model()

        if model == "mock":
            return [
                EmbeddedChunk(chunk=c, vector=self._mock_vector(c.content))
                for c in chunks
            ]

        results = []
        texts   = [self._prepare_text(c) for c in chunks]

        for i in range(0, len(texts), batch_size):
            batch_texts  = texts[i : i + batch_size]
            batch_chunks = chunks[i : i + batch_size]

            vectors = model.encode(
                batch_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()

            for chunk, vector in zip(batch_chunks, vectors):
                results.append(EmbeddedChunk(chunk=chunk, vector=vector))

        print(f"[embed] Embedded {len(results)} chunks (local, FREE)")
        return results

    def embed_query(self, query_text: str) -> list[float]:
        model = self._get_model()
        if model == "mock":
            return self._mock_vector(query_text)
        return model.encode(
            [query_text[:_MAX_CHARS]],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        if model == "mock":
            return [self._mock_vector(t) for t in texts]
        return model.encode(
            [t[:_MAX_CHARS] for t in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    def _prepare_text(self, chunk: CodeChunk) -> str:
        header = (
            f"Language: {chunk.language}\n"
            f"File: {chunk.filename}\n"
            f"Section: {chunk.section_type} {chunk.section_name}\n"
            f"Lines {chunk.start_line}-{chunk.end_line}\n\n"
        )
        return header + chunk.content[:(_MAX_CHARS - len(header))]

    def _mock_vector(self, text: str) -> list[float]:
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
        rng  = __import__("random").Random(seed)
        raw  = [rng.gauss(0, 1) for _ in range(self._dim)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]