import fakeredis

from src.storage.idempotency_store import IdempotencyStore


def make_store() -> IdempotencyStore:
    return IdempotencyStore(client=fakeredis.FakeRedis())


def test_first_claim_succeeds():
    store = make_store()
    assert store.try_mark_processed("acme/widgets", "abc123") is True


def test_second_claim_on_same_commit_fails():
    store = make_store()
    store.try_mark_processed("acme/widgets", "abc123")
    assert store.try_mark_processed("acme/widgets", "abc123") is False


def test_different_commit_sha_is_independent():
    store = make_store()
    store.try_mark_processed("acme/widgets", "abc123")
    assert store.try_mark_processed("acme/widgets", "def456") is True


def test_different_repo_same_sha_is_independent():
    store = make_store()
    store.try_mark_processed("acme/widgets", "abc123")
    assert store.try_mark_processed("acme/other-repo", "abc123") is True


def test_claim_sets_a_ttl():
    client = fakeredis.FakeRedis()
    store = IdempotencyStore(client=client)
    store.try_mark_processed("acme/widgets", "abc123", ttl_seconds=100)
    ttl = client.ttl("idempotency:acme/widgets:abc123")
    assert 0 < ttl <= 100


def test_release_allows_a_subsequent_claim():
    store = make_store()
    store.try_mark_processed("acme/widgets", "abc123")
    store.release("acme/widgets", "abc123")
    assert store.try_mark_processed("acme/widgets", "abc123") is True


def test_release_of_unclaimed_commit_is_a_no_op():
    store = make_store()
    store.release("acme/widgets", "never-claimed")  # must not raise
    assert store.try_mark_processed("acme/widgets", "never-claimed") is True
