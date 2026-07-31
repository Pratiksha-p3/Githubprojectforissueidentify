"""
src/core/circuit_breaker.py

Stage 14's "circuit breaker on LLM eval-drift" — protects the pipeline
from a degraded LLM in two distinct senses, both fed into the same
breaker: infra failures (timeouts, rate limits, connection errors —
recorded by src/agents/llm_client.py around the raw provider call) and
output-quality drift (responses that come back 200 OK but aren't usable —
recorded by src/agents/_llm_finding_agent.py when a response fails to
parse as the expected JSON shape). A model that starts silently degrading
in quality without ever raising an exception is exactly the case a
transport-level retry (src/core/backoff.py) cannot catch, since nothing
"failed" from the transport's point of view.

Classic three-state design: CLOSED (normal — every call attempted) -> OPEN
(too many recent failures — calls are short-circuited immediately, no
wasted API round-trip, callers fall back to deterministic-only results the
same way any other LLM outage degrades them) -> HALF_OPEN (cooldown
elapsed — the next call is let through as a trial; success closes the
circuit again, failure reopens it).

State is in-process, not Redis-backed. Each worker process trips and
recovers its own breaker independently, so under real concurrent load
(multiple Celery workers) a Redis-backed provider outage is caught by
each worker separately, on its own schedule, rather than instantly by
all of them at once. That's a real, deliberate limitation for this
stage's scope: a shared breaker would need centralized state, which
duplicates the exact race-condition class Stage 13's IdempotencyStore
fix was about, and is a distinct enough project to defer rather than
bolt on here.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from enum import Enum

from src.core import metrics

DEFAULT_WINDOW_SIZE = 10
DEFAULT_MIN_CALLS = 5
DEFAULT_FAILURE_THRESHOLD = 0.5
DEFAULT_COOLDOWN_SECONDS = 60.0


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised by a caller's allow_request() check when the circuit is
    open — the caller should treat this exactly like any other LLM
    failure (fall back to deterministic-only, mark the review DEGRADED),
    not propagate it as a fatal error."""


class CircuitBreaker:
    def __init__(
        self,
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
        min_calls: int = DEFAULT_MIN_CALLS,
        failure_threshold: float = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._window: deque[bool] = deque(maxlen=window_size)
        self._min_calls = min_calls
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow_request(self) -> bool:
        """Call before attempting an LLM call. False means: don't even
        try, the circuit is open and cooldown hasn't elapsed yet."""
        if self._state != CircuitState.OPEN:
            return True
        if self._clock() - self._opened_at >= self._cooldown_seconds:
            self._state = CircuitState.HALF_OPEN
            return True
        return False

    def record_success(self) -> None:
        self._window.append(True)
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._window.clear()

    def record_failure(self) -> None:
        self._window.append(False)
        if self._state == CircuitState.HALF_OPEN:
            self._open()
            return
        if len(self._window) >= self._min_calls:
            failure_rate = self._window.count(False) / len(self._window)
            if failure_rate >= self._failure_threshold:
                self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._window.clear()
        metrics.increment("circuit_breaker_opened_total")

    def reset(self) -> None:
        """Back to a fresh CLOSED state with an empty window. Real callers
        never need this (the breaker only ever moves forward through its
        own state machine) — it exists for tests that exercise the
        process-wide singleton `breaker` and need a clean slate between
        cases, without swapping in a whole new instance."""
        self._window.clear()
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0


# Process-wide singleton — every LLM call in this process goes through the
# same breaker, matching src/agents/llm_client.py's single-dispatch-point
# design (one choke point to protect, not N independently-tracked ones).
breaker = CircuitBreaker()
