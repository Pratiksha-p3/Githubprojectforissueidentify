import fakeredis

from src.storage.dlq_store import DLQStore


def make_store() -> DLQStore:
    return DLQStore(client=fakeredis.FakeRedis())


def test_empty_dlq_initially():
    store = make_store()
    assert store.count() == 0
    assert store.all() == []


def test_push_then_appears_in_all():
    store = make_store()
    store.push(repo="acme/widgets", commit_sha="abc123", error="boom")

    entries = store.all()
    assert len(entries) == 1
    assert entries[0]["repo"] == "acme/widgets"
    assert entries[0]["commit_sha"] == "abc123"
    assert entries[0]["error"] == "boom"
    assert "failed_at" in entries[0]


def test_multiple_pushes_preserve_order_and_count():
    store = make_store()
    store.push(repo="acme/widgets", commit_sha="first", error="e1")
    store.push(repo="acme/widgets", commit_sha="second", error="e2")

    assert store.count() == 2
    entries = store.all()
    assert entries[0]["commit_sha"] == "first"
    assert entries[1]["commit_sha"] == "second"
