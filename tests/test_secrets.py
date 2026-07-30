import sys
import types

import pytest

from src.core.config import settings
from src.core.secrets import (
    AzureKeyVaultBackend,
    EnvBackend,
    get_backend,
    get_secret,
    resolve_secrets,
)


def test_env_backend_reads_os_environ(monkeypatch):
    monkeypatch.setenv("MY_TEST_SECRET", "value123")
    assert EnvBackend().get_secret("MY_TEST_SECRET") == "value123"


def test_env_backend_returns_none_for_missing_var(monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET_VAR", raising=False)
    assert EnvBackend().get_secret("DEFINITELY_NOT_SET_VAR") is None


def test_get_backend_defaults_to_env(monkeypatch):
    monkeypatch.setattr(settings, "secrets_backend", "env")
    assert isinstance(get_backend(), EnvBackend)


def test_get_backend_returns_azure_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "secrets_backend", "azure_keyvault")
    monkeypatch.setattr(settings, "azure_keyvault_url", "https://myvault.vault.azure.net")
    assert isinstance(get_backend(), AzureKeyVaultBackend)


def test_azure_backend_requires_vault_url(monkeypatch):
    monkeypatch.setattr(settings, "azure_keyvault_url", "")
    with pytest.raises(RuntimeError, match="AZURE_KEYVAULT_URL"):
        AzureKeyVaultBackend()


def test_azure_backend_get_secret_returns_value(monkeypatch):
    class _FakeSecretValue:
        def __init__(self, value):
            self.value = value

    class _FakeSecretClient:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            return _FakeSecretValue(f"secret-for-{name}")

    fake_identity_module = types.ModuleType("azure.identity")
    fake_identity_module.DefaultAzureCredential = lambda: None  # type: ignore[attr-defined]
    fake_keyvault_module = types.ModuleType("azure.keyvault.secrets")
    fake_keyvault_module.SecretClient = _FakeSecretClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity_module)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", fake_keyvault_module)

    backend = AzureKeyVaultBackend(vault_url="https://myvault.vault.azure.net")
    assert backend.get_secret("MY_SECRET") == "secret-for-MY_SECRET"


def test_azure_backend_get_secret_returns_none_on_failure(monkeypatch):
    backend = AzureKeyVaultBackend(vault_url="https://myvault.vault.azure.net")

    def _raise_client(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(AzureKeyVaultBackend, "_get_client", _raise_client)
    assert backend.get_secret("MY_SECRET") is None


def test_resolve_secrets_is_noop_for_env_backend():
    class _Dummy:
        groq_api_key = "original"

    dummy = _Dummy()
    resolve_secrets(dummy, backend=EnvBackend())
    assert dummy.groq_api_key == "original"


def test_resolve_secrets_overrides_fields_with_backend_values():
    class _FakeBackend:
        def get_secret(self, name):
            return "overridden-value" if name == "GROQ_API_KEY" else None

    class _Dummy:
        groq_api_key = "original"
        openai_api_key = "unchanged"

    dummy = _Dummy()
    resolve_secrets(dummy, backend=_FakeBackend())

    assert dummy.groq_api_key == "overridden-value"
    assert dummy.openai_api_key == "unchanged"


def test_get_secret_uses_configured_backend(monkeypatch):
    monkeypatch.setattr(settings, "secrets_backend", "env")
    monkeypatch.setenv("SOME_KEY", "some-value")
    assert get_secret("SOME_KEY") == "some-value"
