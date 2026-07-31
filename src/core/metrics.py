"""
src/core/metrics.py

Stage 14's minimal in-process metrics registry — deliberately not
prometheus-client: this project has stayed lean on dependencies
throughout (see e.g. the semgrep isolation note in README.md), and a
plain counter dict exposed as JSON via a /metrics endpoint covers what's
actually needed to answer "is this worker healthy" without adding a new
dependency and a new exposition-format concern for a single stage.

Same in-process-singleton tradeoff as src/core/circuit_breaker.py:
counters are per-process, not aggregated across Celery workers. A real
production deployment would scrape each worker's /metrics separately (or
push to a real aggregator) rather than expect one process's counters to
represent the whole fleet — noted here rather than silently assumed away.
"""
from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_counters: Counter[str] = Counter()


def increment(name: str, by: int = 1) -> None:
    with _lock:
        _counters[name] += by


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def reset() -> None:
    """Not used by real callers (counters accumulate for the life of the
    process) -- exists for test isolation, same as
    src/core/circuit_breaker.py's CircuitBreaker.reset()."""
    with _lock:
        _counters.clear()
