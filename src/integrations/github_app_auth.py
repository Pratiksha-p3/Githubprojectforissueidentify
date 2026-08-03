"""
src/integrations/github_app_auth.py

GitHub App authentication: signs a short-lived JWT with the App's private
key (RS256, iss=app_id, expires well under GitHub's 10-minute cap), then
exchanges it for an installation access token (valid ~1 hour) via
POST /app/installations/{installation_id}/access_tokens.

This is the ONLY auth mode the Check Runs API accepts — a personal
access token is rejected with a 403 regardless of what scopes it's
granted, confirmed live against a real repo (see src/integrations/
github_client.py's docstring). GitHub App auth is what actually closes
that gap.

`PyJWT[crypto]` is an optional dependency (`pip install -e ".[github-app]"`)
imported lazily inside the functions that need it, not at module import
time — same pattern as src/core/secrets.py's Azure Key Vault backend, so
a deployment that only ever uses PAT auth (the default) never needs
these packages installed at all.

Installation tokens are cached per (app_id, installation_id) and reused
until close to expiry, rather than minting a fresh one on every API call
— GitHub rate-limits token creation, and a new JWT+exchange round trip
on every single request would be wasteful for what's meant to be an
hour-long credential.
"""
from __future__ import annotations

import datetime
import time
from collections.abc import Callable
from typing import Any

_API_BASE = "https://api.github.com"
_JWT_TTL_SECONDS = 9 * 60  # under GitHub's 10-minute cap, with margin
_CLOCK_SKEW_MARGIN_SECONDS = 60  # iat slightly in the past, tolerates drift
_TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60  # refresh 5 min before actual expiry

# (app_id, installation_id) -> (token, expires_at_unix_ts). Process-wide,
# same in-process-singleton tradeoff as src/core/circuit_breaker.py's
# breaker and src/core/metrics.py's counters -- each worker process
# caches (and refreshes) its own installation token independently.
_installation_token_cache: dict[str, tuple[str, float]] = {}


def generate_jwt(app_id: str, private_key_pem: str, *, now: float | None = None) -> str:
    """A JWT identifying the App itself (not any specific installation)
    — used only to request an installation access token below, never
    sent to any other GitHub endpoint."""
    import jwt

    issued_at = int((now if now is not None else time.time()) - _CLOCK_SKEW_MARGIN_SECONDS)
    payload = {"iat": issued_at, "exp": issued_at + _JWT_TTL_SECONDS, "iss": app_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def get_installation_token(
    app_id: str,
    installation_id: str,
    private_key_pem: str,
    *,
    http_post: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.time,
) -> str:
    """Returns a cached installation token if it's not close to expiry,
    otherwise mints a fresh JWT and exchanges it for a new one.
    `http_post` is injectable so tests can exercise this without a real
    network call — defaults to `requests.post`, imported lazily for the
    same reason `jwt` is."""
    if http_post is None:
        import requests

        http_post = requests.post

    cache_key = f"{app_id}:{installation_id}"
    now = clock()
    cached = _installation_token_cache.get(cache_key)
    if cached is not None:
        token, expires_at = cached
        if now < expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
            return token

    app_jwt = generate_jwt(app_id, private_key_pem, now=now)
    resp = http_post(
        f"{_API_BASE}/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["token"]
    expires_at = _parse_github_timestamp(data["expires_at"])
    _installation_token_cache[cache_key] = (token, expires_at)
    return token


def _parse_github_timestamp(value: str) -> float:
    """GitHub returns e.g. "2026-08-03T12:00:00Z" — always UTC, always
    this exact format, for this specific endpoint's expires_at field."""
    dt = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.UTC
    )
    return dt.timestamp()


def reset_cache() -> None:
    """Not used by real callers — exists for test isolation, same
    pattern as src/core/circuit_breaker.py's CircuitBreaker.reset()."""
    _installation_token_cache.clear()
