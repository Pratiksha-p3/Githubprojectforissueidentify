"""
vectordb/factory.py

Single place that decides which vector store backs the RAG pipeline.
Defaults to ChromaStore (free, local, no signup). Set
VECTOR_DB_PROVIDER=pinecone + PINECONE_API_KEY to switch to Pinecone —
every caller goes through get_vector_store() instead of constructing
ChromaStore()/PineconeStore() directly, so that env var is a real switch.
"""
from __future__ import annotations

from config import cfg


def get_vector_store():
    provider = (cfg.vector_db_provider or "chroma").lower()

    if provider == "pinecone":
        if not cfg.pinecone_api_key:
            print("[vectordb] VECTOR_DB_PROVIDER=pinecone but PINECONE_API_KEY is not "
                  "set — falling back to ChromaDB.")
        else:
            from vectordb.pinecone_store import PineconeStore
            return PineconeStore()

    from vectordb.chroma_store import ChromaStore
    return ChromaStore()
