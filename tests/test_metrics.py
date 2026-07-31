from src.core import metrics


def setup_function():
    metrics.reset()


def test_increment_starts_a_new_counter_at_one():
    metrics.increment("reviews_completed_total")
    assert metrics.snapshot()["reviews_completed_total"] == 1


def test_increment_accumulates():
    metrics.increment("dlq_pushes_total")
    metrics.increment("dlq_pushes_total")
    metrics.increment("dlq_pushes_total")
    assert metrics.snapshot()["dlq_pushes_total"] == 3


def test_increment_by_custom_amount():
    metrics.increment("findings_total", by=5)
    assert metrics.snapshot()["findings_total"] == 5


def test_snapshot_is_independent_counters_per_name():
    metrics.increment("reviews_completed_total")
    metrics.increment("reviews_degraded_total")
    metrics.increment("reviews_degraded_total")
    snap = metrics.snapshot()
    assert snap["reviews_completed_total"] == 1
    assert snap["reviews_degraded_total"] == 2


def test_reset_clears_all_counters():
    metrics.increment("reviews_completed_total")
    metrics.reset()
    assert metrics.snapshot() == {}


def test_snapshot_does_not_expose_the_live_counter_for_mutation():
    metrics.increment("a")
    snap = metrics.snapshot()
    snap["a"] = 999
    assert metrics.snapshot()["a"] == 1
