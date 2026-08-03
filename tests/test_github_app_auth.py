"""
tests/test_github_app_auth.py

Exercises real JWT signing/verification (a real RSA keypair generated
once per test session via a fixture, not a hardcoded fake key) rather
than mocking `jwt.encode` itself -- a mock could pass while the actual
signing call is subtly wrong (bad algorithm, missing claim) and this
suite would never catch it. Only the HTTP call to GitHub's token
exchange endpoint is faked (via the injectable `http_post`), since that
part genuinely needs a real GitHub App installation to test for real.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.integrations import github_app_auth as app_auth


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_key = private_key.public_key()
    return private_pem, public_key


@pytest.fixture(autouse=True)
def _reset_cache():
    app_auth.reset_cache()
    yield
    app_auth.reset_cache()


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} error")

    def json(self) -> dict:
        return self._json_data


def test_generate_jwt_is_signed_with_the_real_private_key_and_verifiable(keypair):
    import jwt

    private_pem, public_key = keypair

    token = app_auth.generate_jwt("12345", private_pem, now=1_700_000_000.0)

    # verify_exp=False since `now` above is a fixed test timestamp in the
    # past relative to wall-clock time -- signature/issuer validity is
    # what this test checks, not real-time expiry (covered separately).
    decoded = jwt.decode(
        token, public_key, algorithms=["RS256"], issuer="12345",
        options={"verify_exp": False},
    )
    assert decoded["iss"] == "12345"


def test_generate_jwt_exp_is_within_githubs_ten_minute_cap(keypair):
    import jwt

    private_pem, public_key = keypair

    token = app_auth.generate_jwt("12345", private_pem, now=1_700_000_000.0)

    decoded = jwt.decode(
        token, public_key, algorithms=["RS256"], issuer="12345",
        options={"verify_exp": False},
    )
    assert decoded["exp"] - decoded["iat"] < 10 * 60


def test_generate_jwt_iat_is_backdated_for_clock_skew_tolerance(keypair):
    import jwt

    private_pem, public_key = keypair
    now = 1_700_000_000.0

    token = app_auth.generate_jwt("12345", private_pem, now=now)

    decoded = jwt.decode(
        token, public_key, algorithms=["RS256"], issuer="12345",
        options={"verify_exp": False},
    )
    assert decoded["iat"] < int(now)


def test_get_installation_token_exchanges_jwt_for_installation_token(keypair):
    private_pem, _public_key = keypair
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(
            {"token": "ghs_installation_token", "expires_at": "2026-08-03T13:00:00Z"}
        )

    token = app_auth.get_installation_token(
        "12345", "999", private_pem, http_post=fake_post, clock=lambda: 1_754_218_800.0,
    )

    assert token == "ghs_installation_token"
    url, kwargs = calls[0]
    assert url == "https://api.github.com/app/installations/999/access_tokens"
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")


def test_installation_token_is_cached_and_not_re_requested(keypair):
    private_pem, _public_key = keypair
    calls = []

    def fake_post(url, **kwargs):
        calls.append(1)
        return _FakeResponse({"token": "ghs_token", "expires_at": "2026-08-03T14:00:00Z"})

    clock_value = [1_754_218_800.0]  # 2025-08-03T12:00:00Z-ish
    token1 = app_auth.get_installation_token(
        "12345", "999", private_pem, http_post=fake_post, clock=lambda: clock_value[0],
    )
    clock_value[0] += 60  # one minute later -- well within the 1-hour lifetime
    token2 = app_auth.get_installation_token(
        "12345", "999", private_pem, http_post=fake_post, clock=lambda: clock_value[0],
    )

    assert token1 == token2 == "ghs_token"
    assert len(calls) == 1  # second call served entirely from cache


def test_installation_token_is_refreshed_close_to_expiry(keypair):
    private_pem, _public_key = keypair
    calls = []
    responses = iter(
        [
            _FakeResponse({"token": "ghs_first", "expires_at": "2026-08-03T13:00:00Z"}),
            _FakeResponse({"token": "ghs_second", "expires_at": "2026-08-03T14:00:00Z"}),
        ]
    )

    def fake_post(url, **kwargs):
        calls.append(1)
        return next(responses)

    # expires_at above is 2026-08-03T13:00:00Z = 1785848400.0
    first_token = app_auth.get_installation_token(
        "12345", "999", private_pem, http_post=fake_post, clock=lambda: 1785848400.0 - 600,
    )
    # Now within the refresh margin (5 min) of that same expiry.
    second_token = app_auth.get_installation_token(
        "12345", "999", private_pem, http_post=fake_post, clock=lambda: 1785848400.0 - 60,
    )

    assert first_token == "ghs_first"
    assert second_token == "ghs_second"
    assert len(calls) == 2


def test_different_installations_are_cached_independently(keypair):
    private_pem, _public_key = keypair
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        installation_id = url.rsplit("/", 2)[1]
        return _FakeResponse(
            {"token": f"ghs_{installation_id}", "expires_at": "2026-08-03T13:00:00Z"}
        )

    token_a = app_auth.get_installation_token(
        "12345", "111", private_pem, http_post=fake_post, clock=lambda: 1_754_218_800.0,
    )
    token_b = app_auth.get_installation_token(
        "12345", "222", private_pem, http_post=fake_post, clock=lambda: 1_754_218_800.0,
    )

    assert token_a == "ghs_111"
    assert token_b == "ghs_222"
    assert len(calls) == 2


def test_raises_on_a_failed_token_exchange(keypair):
    private_pem, _public_key = keypair

    def fake_post(url, **kwargs):
        return _FakeResponse({}, status_code=401)

    with pytest.raises(RuntimeError):
        app_auth.get_installation_token(
            "12345", "999", private_pem, http_post=fake_post, clock=lambda: 1_754_218_800.0,
        )
