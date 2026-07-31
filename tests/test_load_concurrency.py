"""
tests/test_load_concurrency.py

A concurrency-correctness harness standing in for the plan's "load
testing: simulate concurrent PR reviews across multiple repos" — this
project doesn't have a live Celery/Redis stack to throw real concurrent
load at in this environment (Docker wasn't available; see README), so
this instead directly tests the concurrency-sensitive correctness
property the async design depends on: two "workers" processing the
same commit at the same time must not both do the real work.

fakeredis + real Python threads exercises genuine interleaving (the GIL
switches between threads at bytecode boundaries), so this is a real
concurrency test, not just a sequential one dressed up as one. This
test is what actually caught a real bug: the original two-step
"already_processed() then mark_processed()" API let all 20 concurrent
callers through (see the Stage 13 commit) — src/storage/
idempotency_store.py's try_mark_processed() is the atomic (single
Redis SET...NX call) fix, exercised here.
"""
from __future__ import annotations

import threading
import time

import fakeredis

from src.storage.idempotency_store import IdempotencyStore


def test_concurrent_workers_on_the_same_commit_only_do_the_work_once():
    store = IdempotencyStore(client=fakeredis.FakeRedis())
    work_done_count = 0
    lock = threading.Lock()

    def worker():
        nonlocal work_done_count
        if not store.try_mark_processed("acme/widgets", "same-commit-sha"):
            return
        # A real review takes real time (LLM calls, checkers, etc.) --
        # simulated here to widen the window a race would need to slip
        # through, even though try_mark_processed() itself is already
        # atomic and shouldn't need this to be correct.
        time.sleep(0.01)
        with lock:
            work_done_count += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert work_done_count == 1, (
        f"Expected exactly 1 worker to do the real work for this commit, "
        f"but {work_done_count} did -- try_mark_processed() is not "
        f"actually atomic under concurrent access."
    )


def test_concurrent_workers_on_different_commits_all_proceed():
    store = IdempotencyStore(client=fakeredis.FakeRedis())
    work_done = []
    lock = threading.Lock()

    def worker(commit_sha: str):
        if not store.try_mark_processed("acme/widgets", commit_sha):
            return
        time.sleep(0.01)
        with lock:
            work_done.append(commit_sha)

    threads = [threading.Thread(target=worker, args=(f"commit-{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(work_done) == 10  # distinct commits are independent, all proceed
