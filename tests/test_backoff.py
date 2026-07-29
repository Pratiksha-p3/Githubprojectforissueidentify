import pytest

from src.core.backoff import RetriesExhausted, call_with_backoff


def test_succeeds_immediately_without_retrying():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    result = call_with_backoff(fn, sleep_fn=lambda _s: None)
    assert result == "ok"
    assert len(calls) == 1


def test_retries_and_eventually_succeeds():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("transient")
        return "ok"

    result = call_with_backoff(fn, max_attempts=3, sleep_fn=lambda _s: None)
    assert result == "ok"
    assert len(calls) == 3


def test_raises_retries_exhausted_after_max_attempts():
    def fn():
        raise ConnectionError("always fails")

    with pytest.raises(RetriesExhausted) as exc_info:
        call_with_backoff(fn, max_attempts=3, sleep_fn=lambda _s: None)
    assert exc_info.value.attempts == 3


def test_non_retryable_exception_raised_immediately():
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("invalid api key")

    with pytest.raises(ValueError):
        call_with_backoff(
            fn,
            max_attempts=5,
            should_retry=lambda e: "rate_limit" in str(e),
            sleep_fn=lambda _s: None,
        )
    assert len(calls) == 1


def test_sleep_durations_follow_exponential_backoff():
    sleeps: list[float] = []

    def fn():
        raise ConnectionError("transient")

    with pytest.raises(RetriesExhausted):
        call_with_backoff(
            fn, max_attempts=3, base_delay_seconds=1.0, sleep_fn=sleeps.append
        )
    assert sleeps == [1.0, 2.0]
