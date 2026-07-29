"""
src/core/backoff.py

Shared retry-with-exponential-backoff, used by the LLM client (Stage 2)
and later reused as-is by the GitHub/NVD/OSV clients (Stage 5/7) instead
of each call site rolling its own retry loop.

`sleep_fn` is injectable so tests can assert retry behavior without
actually waiting — real callers just use the default `time.sleep`.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RetriesExhausted(Exception):
    """All attempts failed; wraps the last underlying exception."""

    def __init__(self, attempts: int, last_error: Exception):
        super().__init__(f"Gave up after {attempts} attempt(s): {last_error}")
        self.attempts = attempts
        self.last_error = last_error


def call_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    should_retry: Callable[[Exception], bool] = lambda _e: True,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """
    Calls `fn()`, retrying on failure up to `max_attempts` total tries with
    exponential backoff (base_delay * 2**attempt_index between attempts).
    `should_retry(exc)` decides whether a given exception is worth
    retrying at all (e.g. a rate limit is, an invalid-API-key error isn't)
    — a non-retryable exception is re-raised immediately on first
    occurrence, without exhausting attempts or sleeping.
    """
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - intentionally broad, re-raised below
            last_error = e
            if not should_retry(e):
                raise
            if attempt < max_attempts - 1:
                sleep_fn(base_delay_seconds * (2**attempt))

    assert last_error is not None  # loop always runs at least once
    raise RetriesExhausted(max_attempts, last_error)
