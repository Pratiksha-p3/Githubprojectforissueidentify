"""
src/core/secrets.py

get_secret(name) is the single interface every secret read can go
through, with pluggable backends behind it:

  EnvBackend           — reads os.environ (populated by .env via
                         python-dotenv, the same source
                         src.core.config.Settings itself loads from).
                         This is the default, and the only backend
                         actually exercised in this project — no Azure
                         subscription/credentials were available in
                         this session.
  AzureKeyVaultBackend — reads from a real Azure Key Vault via
                         azure-identity/azure-keyvault-secrets. Coded
                         against the same interface as EnvBackend so
                         switching SECRETS_BACKEND is a config change,
                         not a rewrite of every call site — but NOT
                         exercised against a live vault here, and the
                         azure-* packages are an optional extra
                         (`pip install -e ".[azure]"`), not a core
                         dependency, since most users only need
                         EnvBackend.

resolve_secrets() is the actual integration point: called once at
process startup (src/cli/main.py), it overrides the secret-ish fields
already loaded onto the global `settings` object (src.core.config) with
values from the configured backend — a no-op when SECRETS_BACKEND=env
(the default), since EnvBackend reads the same os.environ Settings
already populated itself from.
"""
from __future__ import annotations

import os
from typing import Protocol

from src.core.config import settings

# The fields on Settings that are genuine secrets (API keys, tokens) —
# not general config (URLs, model names, feature flags) — and therefore
# the ones a real secrets-manager backend should be allowed to override.
_SECRET_FIELD_NAMES = (
    "groq_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "github_token",
    "github_webhook_secret",
    "gitlab_token",
    "gitlab_webhook_secret",
    "nvd_api_key",
    "slack_webhook_url",
    "teams_webhook_url",
    "jira_api_token",
)


class SecretsBackend(Protocol):
    def get_secret(self, name: str) -> str | None: ...


class EnvBackend:
    def get_secret(self, name: str) -> str | None:
        return os.environ.get(name)


class AzureKeyVaultBackend:
    """
    Reads from Azure Key Vault. Authentication uses DefaultAzureCredential
    (managed identity when running in Azure, `az login` locally, or an
    env-var service principal — whichever is available first). Never
    raises out of get_secret() — a lookup failure returns None, logged,
    same "best-effort, never a hard dependency" pattern this project
    uses for every optional external call.
    """

    def __init__(self, vault_url: str | None = None):
        self._vault_url = vault_url or settings.azure_keyvault_url
        if not self._vault_url:
            raise RuntimeError(
                "SECRETS_BACKEND=azure_keyvault but AZURE_KEYVAULT_URL is not set"
            )
        self._client = None  # lazily constructed — azure-* SDKs are an optional extra

    def _get_client(self):
        if self._client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._client = SecretClient(
                vault_url=self._vault_url, credential=DefaultAzureCredential()
            )
        return self._client

    def get_secret(self, name: str) -> str | None:
        try:
            return self._get_client().get_secret(name).value
        except Exception as e:
            print(f"[secrets] Azure Key Vault lookup failed for {name!r}: {e}")
            return None


def get_backend() -> SecretsBackend:
    backend_name = (settings.secrets_backend or "env").lower()
    if backend_name == "azure_keyvault":
        return AzureKeyVaultBackend()
    return EnvBackend()


def get_secret(name: str) -> str | None:
    return get_backend().get_secret(name)


def resolve_secrets(settings_obj, backend: SecretsBackend | None = None) -> None:
    """
    Overrides `settings_obj`'s secret-ish fields from the configured
    backend, in place, for every field where the backend actually has a
    value. Call once at process startup — see src/cli/main.py.
    """
    backend = backend or get_backend()
    if isinstance(backend, EnvBackend):
        return  # already loaded via pydantic-settings; nothing to add

    for field_name in _SECRET_FIELD_NAMES:
        value = backend.get_secret(field_name.upper())
        if value:
            setattr(settings_obj, field_name, value)
