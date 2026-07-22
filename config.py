"""
config.py — FREE version
LLM   : Groq  (free at console.groq.com)
Embed : sentence-transformers (local, free)
No OpenAI / Anthropic key needed.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Groq (free LLM) ──────────────────────────────────
    groq_api_key: str       = os.getenv("GROQ_API_KEY", "")
    review_model: str       = os.getenv("REVIEW_MODEL", "llama-3.3-70b-versatile")
    llm_provider: str       = os.getenv("LLM_PROVIDER", "groq")

    # ── Local embeddings (free, no API key) ──────────────
    embedding_model: str    = "all-MiniLM-L6-v2"

    # ── These are kept so old imports don't crash ─────────
    openai_api_key: str     = ""
    anthropic_api_key: str  = ""

    # ── GitHub (optional) ─────────────────────────────────
    github_app_id: str          = os.getenv("GITHUB_APP_ID", "")
    github_pem_path: str        = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "./github_app.pem")
    github_install_id: str      = os.getenv("GITHUB_INSTALLATION_ID", "")
    github_webhook_secret: str  = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    # ── ChromaDB ─────────────────────────────────────────
    chroma_dir: str             = os.getenv("CHROMA_PERSIST_DIR", "./vectordb/chroma_data")
    chroma_collection: str      = "code_review"

    # ── RAG settings ─────────────────────────────────────
    chunk_size: int             = int(os.getenv("CHUNK_SIZE", "512"))
    chunk_overlap: int          = int(os.getenv("CHUNK_OVERLAP", "128"))
    top_k: int                  = int(os.getenv("TOP_K_RETRIEVAL", "3"))
    min_similarity_score: float = float(os.getenv("MIN_SIMILARITY_SCORE", "0.30"))
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
    max_review_tokens: int      = int(os.getenv("MAX_REVIEW_TOKENS", "4096"))

    # ── Skip patterns ─────────────────────────────────────
    skip_patterns: list         = None

    def __post_init__(self):
        self.skip_patterns = [
            "package-lock.json", "yarn.lock", "poetry.lock",
            "Pipfile.lock", ".lock", "migrations/", "node_modules/",
            "dist/", "build/", "__pycache__/", ".pyc",
            "*.min.js", "*.min.css", "vendor/",
        ]

    @property
    def use_groq(self) -> bool:
        return bool(self.groq_api_key)


cfg = Config()