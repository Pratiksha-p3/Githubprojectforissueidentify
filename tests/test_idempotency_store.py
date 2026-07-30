import fakeredis

from src.storage.idempotency_store import IdempotencyStore


def make_store() -> IdempotencyStore:
    return IdempotencyStore(client=fakeredis.FakeRedis())


def test_not_processed_initially():
    store = make_store()
    assert store.already_processed("acme/widgets", "abc123") is False


def test_mark_processed_then_already_processed_is_true():
    store = make_store()
    store.mark_processed("acme/widgets", "abc123")
    assert store.already_processed("acme/widgets", "abc123") is True


def test_different_commit_sha_is_independent():
    store = make_store()
    store.mark_processed("acme/widgets", "abc123")
    assert store.already_processed("acme/widgets", "def456") is False


def test_different_repo_same_sha_is_independent():
    store = make_store()
    store.mark_processed("acme/widgets", "abc123")
    assert store.already_processed("acme/other-repo", "abc123") is False


def test_mark_processed_sets_a_ttl():
    client = fakeredis.FakeRedis()
    store = IdempotencyStore(client=client)
    store.mark_processed("acme/widgets", "abc123", ttl_seconds=100)
    ttl = client.ttl("idempotency:acme/widgets:abc123")
    assert 0 < ttl <= 100
