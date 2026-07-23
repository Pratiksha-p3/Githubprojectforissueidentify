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

    # ── Optional paid providers — only used when LLM_PROVIDER selects
    #    them; the app runs entirely on Groq without these set. ───────
    openai_api_key: str     = os.getenv("OPENAI_API_KEY", "")
    openai_model: str       = os.getenv("OPENAI_MODEL", "gpt-4o")
    anthropic_api_key: str  = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str    = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    # ── GitHub (optional) ─────────────────────────────────
    github_app_id: str          = os.getenv("GITHUB_APP_ID", "")
    github_pem_path: str        = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "./github_app.pem")
    github_install_id: str      = os.getenv("GITHUB_INSTALLATION_ID", "")
    github_webhook_secret: str  = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    # ── GitLab (optional — mirrors the GitHub integration) ────
    gitlab_token: str           = os.getenv("GITLAB_TOKEN", "")
    gitlab_url: str              = os.getenv("GITLAB_URL", "https://gitlab.com")
    gitlab_webhook_secret: str  = os.getenv("GITLAB_WEBHOOK_SECRET", "")

    # ── SonarQube (optional — second static analyzer alongside Semgrep) ──
    sonar_token: str             = os.getenv("SONAR_TOKEN", "")
    sonar_host_url: str          = os.getenv("SONAR_HOST_URL", "")
    sonar_project_key: str       = os.getenv("SONAR_PROJECT_KEY", "")

    # ── ChromaDB (default, free, local) ───────────────────
    chroma_dir: str             = os.getenv("CHROMA_PERSIST_DIR", "./vectordb/chroma_data")
    chroma_collection: str      = "code_review"

    # ── Postgres (optional — persists review reports; falls back to the
    #    existing reports/*.json files when unset) ─────────────────────
    database_url: str           = os.getenv("DATABASE_URL", "")

    # ── Redis (optional — caches LLM/retrieval calls; falls back to an
    #    in-process no-op cache when unset) ───────────────────────────
    redis_url: str              = os.getenv("REDIS_URL", "")
    cache_ttl_seconds: int      = int(os.getenv("CACHE_TTL_SECONDS", "86400"))

    # ── Temporal (optional — wraps the review pipeline as a workflow
    #    for retries/observability; the CLI/webhook path works without it) ─
    temporal_address: str       = os.getenv("TEMPORAL_ADDRESS", "")
    temporal_task_queue: str    = os.getenv("TEMPORAL_TASK_QUEUE", "ai-code-review")

    # ── Pinecone (optional alternative — VECTOR_DB_PROVIDER=pinecone) ──
    vector_db_provider: str     = os.getenv("VECTOR_DB_PROVIDER", "chroma")
    pinecone_api_key: str       = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str         = os.getenv("PINECONE_INDEX", "ai-code-review")
    pinecone_namespace: str     = os.getenv("PINECONE_NAMESPACE", "code")
    pinecone_cloud: str         = os.getenv("PINECONE_CLOUD", "aws")
    pinecone_region: str        = os.getenv("PINECONE_REGION", "us-east-1")
    pinecone_dimension: int     = int(os.getenv("PINECONE_DIMENSION", "384"))

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