import fakeredis

from src.core import health
from src.core.circuit_breaker import CircuitState, breaker


def test_check_redis_ok_with_a_reachable_client():
    result = health.check_redis(client=fakeredis.FakeRedis())
    assert result == {"status": "ok"}


def test_check_redis_unreachable_is_reported_not_raised():
    class _BrokenClient:
        def ping(self):
            raise ConnectionError("could not connect to redis")

    result = health.check_redis(client=_BrokenClient())
    assert result["status"] == "unreachable"
    assert "could not connect" in result["error"]


def test_check_postgres_ok_with_a_working_connect_fn():
    class _FakeConn:
        def close(self):
            pass

    result = health.check_postgres(connect_fn=lambda: _FakeConn())
    assert result == {"status": "ok"}


def test_check_postgres_unreachable_is_reported_not_raised():
    def _raise():
        raise ConnectionError("could not connect to postgres")

    result = health.check_postgres(connect_fn=_raise)
    assert result["status"] == "unreachable"
    assert "could not connect" in result["error"]


def test_check_llm_circuit_breaker_reports_current_state():
    breaker.reset()
    assert health.check_llm_circuit_breaker() == {"state": "closed"}

    breaker._state = CircuitState.OPEN  # simulate an open circuit directly
    assert health.check_llm_circuit_breaker() == {"state": "open"}
    breaker.reset()


def test_full_report_is_ok_when_all_dependencies_are_reachable():
    class _FakeConn:
        def close(self):
            pass

    report = health.full_health_report(
        redis_client=fakeredis.FakeRedis(), postgres_connect_fn=lambda: _FakeConn()
    )
    assert report["status"] == "ok"
    assert report["checks"]["redis"]["status"] == "ok"
    assert report["checks"]["postgres"]["status"] == "ok"


def test_full_report_is_degraded_when_redis_is_unreachable():
    class _BrokenClient:
        def ping(self):
            raise ConnectionError("down")

    class _FakeConn:
        def close(self):
            pass

    report = health.full_health_report(
        redis_client=_BrokenClient(), postgres_connect_fn=lambda: _FakeConn()
    )
    assert report["status"] == "degraded"
    assert report["checks"]["redis"]["status"] == "unreachable"


def test_full_report_is_degraded_when_postgres_is_unreachable():
    def _raise():
        raise ConnectionError("down")

    report = health.full_health_report(
        redis_client=fakeredis.FakeRedis(), postgres_connect_fn=_raise
    )
    assert report["status"] == "degraded"
    assert report["checks"]["postgres"]["status"] == "unreachable"


def test_circuit_breaker_state_does_not_affect_overall_status():
    """An OPEN breaker means the pipeline is correctly degrading, not
    that the process itself is unhealthy -- it must not flip overall
    status to degraded on its own."""

    class _FakeConn:
        def close(self):
            pass

    breaker._state = CircuitState.OPEN
    report = health.full_health_report(
        redis_client=fakeredis.FakeRedis(), postgres_connect_fn=lambda: _FakeConn()
    )
    assert report["status"] == "ok"
    assert report["checks"]["llm_circuit_breaker"]["state"] == "open"
    breaker.reset()
