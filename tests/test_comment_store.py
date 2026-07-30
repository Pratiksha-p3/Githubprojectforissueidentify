import fakeredis

from src.storage.comment_store import CommentStore


def make_store() -> CommentStore:
    return CommentStore(client=fakeredis.FakeRedis())


def test_no_comment_id_initially():
    store = make_store()
    assert store.get_comment_id("acme/widgets", "abc123") is None


def test_set_then_get_roundtrips():
    store = make_store()
    store.set_comment_id("acme/widgets", "abc123", 999)
    assert store.get_comment_id("acme/widgets", "abc123") == 999


def test_different_commit_sha_is_independent():
    store = make_store()
    store.set_comment_id("acme/widgets", "abc123", 999)
    assert store.get_comment_id("acme/widgets", "def456") is None
