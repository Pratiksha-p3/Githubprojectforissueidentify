"""
src/core/config.py

Single settings object every later stage reads through, instead of scattered
os.getenv() calls. This indirection is what lets Stage 10 swap the secrets
backend (env vars now, Azure Key Vault later) behind one interface instead
of hunting down every read site across the codebase.

Nothing here is required to be set for Stage 0 — every field has a default
or is optional, since no stage that actually needs these values exists yet.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM provider (Stage 2)
    llm_provider: str = "groq"
    groq_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    review_model: str = "llama-3.1-8b-instant"
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-sonnet-4-20250514"
    max_review_tokens: int = 4000

    # GitHub App (Stage 5)
    github_app_id: str = ""
    github_installation_id: str = ""
    github_app_private_key_path: str = "secrets/github_app.pem"
    github_webhook_secret: str = ""
    github_token: str = ""

    # GitLab (Stage 5)
    gitlab_token: str = ""
    gitlab_webhook_secret: str = ""

    # Async queue (Stage 4)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # State / audit (Stage 4/8/10)
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ai_code_review"

    # RAG (Stage 6)
    chroma_persist_dir: str = "./vectordb/chroma_data"
    min_similarity_score: float = 0.30
    top_k_retrieval: int = 5
    embedding_provider: str = "local"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Enrichment (Stage 7)
    nvd_api_key: str = ""

    # Notifications (Stage 9)
    slack_webhook_url: str = ""
    teams_webhook_url: str = ""
    jira_base_url: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""

    # Secrets backend (Stage 10)
    secrets_backend: str = "env"
    azure_keyvault_url: str = ""

    # Monitoring (Stage 14)
    metrics_enabled: bool = False


settings = Settings()
