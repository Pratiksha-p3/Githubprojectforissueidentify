"""
tests/conftest.py

src/core/circuit_breaker.py's `breaker` is a process-wide singleton (by
design — see its docstring), which means it's also process-wide across a
single pytest run: any test that exercises the real
src/agents/_llm_finding_agent.py::run_finding_agent() (even with call_llm
itself mocked out, since the success/failure recording lives in
run_finding_agent, not inside call_llm) leaves its mark on the same
breaker instance. Without a reset between tests, a cluster of malformed-
response tests running earlier in the session could trip the breaker
open before an unrelated later test ever gets a chance to run — a classic
shared-global-state flakiness source. Resetting it before every test
removes any dependency on test order or which tests happened to run
first.
"""
from __future__ import annotations

import pytest

from src.core.circuit_breaker import breaker


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    breaker.reset()
    yield
    breaker.reset()
