from src.core.circuit_breaker import CircuitBreaker, CircuitState


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_breaker(**overrides) -> tuple[CircuitBreaker, _FakeClock]:
    clock = _FakeClock()
    defaults = dict(window_size=10, min_calls=5, failure_threshold=0.5, cooldown_seconds=60.0)
    defaults.update(overrides)
    return CircuitBreaker(clock=clock, **defaults), clock


def test_starts_closed_and_allows_requests():
    breaker, _ = make_breaker()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_stays_closed_below_the_minimum_call_count():
    breaker, _ = make_breaker(min_calls=5)
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_stays_closed_when_failure_rate_is_below_threshold():
    breaker, _ = make_breaker(min_calls=5, failure_threshold=0.5)
    breaker.record_success()
    breaker.record_success()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED


def test_opens_when_failure_rate_reaches_threshold():
    breaker, _ = make_breaker(min_calls=5, failure_threshold=0.5)
    breaker.record_success()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_stays_open_before_cooldown_elapses():
    breaker, clock = make_breaker(min_calls=1, failure_threshold=0.5, cooldown_seconds=60.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    clock.advance(30.0)
    assert breaker.allow_request() is False
    assert breaker.state == CircuitState.OPEN


def test_moves_to_half_open_after_cooldown_and_allows_one_trial():
    breaker, clock = make_breaker(min_calls=1, failure_threshold=0.5, cooldown_seconds=60.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    clock.advance(61.0)
    assert breaker.allow_request() is True
    assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_success_closes_the_circuit():
    breaker, clock = make_breaker(min_calls=1, failure_threshold=0.5, cooldown_seconds=60.0)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(61.0)
    breaker.allow_request()  # transitions to HALF_OPEN

    breaker.record_success()

    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_half_open_failure_reopens_the_circuit_immediately():
    breaker, clock = make_breaker(min_calls=1, failure_threshold=0.5, cooldown_seconds=60.0)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(61.0)
    breaker.allow_request()  # transitions to HALF_OPEN

    breaker.record_failure()

    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_reset_returns_to_a_clean_closed_state():
    breaker, _ = make_breaker(min_calls=1, failure_threshold=0.5)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    breaker.reset()

    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_window_is_bounded_so_old_failures_eventually_age_out():
    """A long enough run of recent successes should be able to close the
    gap even after an early burst of failures -- the window is a rolling
    window, not a lifetime failure count."""
    breaker, _ = make_breaker(window_size=5, min_calls=5, failure_threshold=0.5)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    # Not yet at min_calls, so still closed -- then five more successes
    # push the three failures out of a window of size 5 entirely.
    for _ in range(5):
        breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
