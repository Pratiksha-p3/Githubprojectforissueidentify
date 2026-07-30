from datetime import UTC, datetime

from src.core.models import Finding, ReviewResult, ReviewStatus, Severity
from src.storage.postgres_store import PostgresStore


class _FakeCursor:
    """Faithful enough to test PostgresStore's actual filter/sort/limit
    logic (not just "SQL got called"): recognizes the module's three
    known statements and applies the same semantics a real Postgres
    server would. Doesn't validate SQL syntax itself — that needs a real
    server (Docker wasn't available in this environment; see README)."""

    def __init__(self, conn: "_FakeConnection"):
        self._conn = conn
        self._last_result: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None) -> None:
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("CREATE TABLE"):
            return
        if normalized.startswith("INSERT"):
            self._conn.rows.append(params)
            return
        if normalized.startswith("SELECT"):
            repo, limit = params
            matching = [r for r in self._conn.rows if r[0] == repo]
            matching.sort(key=lambda r: r[6], reverse=True)
            self._last_result = [r[:7] for r in matching[:limit]]

    def fetchall(self) -> list[tuple]:
        return self._last_result


class _FakeConnection:
    def __init__(self):
        self.rows: list[tuple] = []
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        pass


def make_result(
    repo="acme/widgets", commit_sha="abc", findings=None, reviewed_at=None
) -> ReviewResult:
    return ReviewResult(
        repo=repo,
        commit_sha=commit_sha,
        status=ReviewStatus.COMPLETED,
        findings=findings or [],
        reviewed_at=reviewed_at or datetime.now(UTC),
    )


def test_save_review_persists_and_commits():
    conn = _FakeConnection()
    store = PostgresStore(conn=conn)

    store.save_review(make_result())

    assert len(conn.rows) == 1
    assert conn.rows[0][0] == "acme/widgets"
    assert conn.commits >= 1


def test_get_history_returns_saved_reviews_for_repo():
    conn = _FakeConnection()
    store = PostgresStore(conn=conn)
    store.save_review(make_result(repo="acme/widgets", commit_sha="c1"))
    store.save_review(make_result(repo="other/repo", commit_sha="c2"))

    history = store.get_history("acme/widgets")

    assert len(history) == 1
    assert history[0]["commit_sha"] == "c1"


def test_get_history_orders_most_recent_first():
    conn = _FakeConnection()
    store = PostgresStore(conn=conn)
    older = datetime(2024, 1, 1, tzinfo=UTC)
    newer = datetime(2024, 6, 1, tzinfo=UTC)
    store.save_review(make_result(commit_sha="old", reviewed_at=older))
    store.save_review(make_result(commit_sha="new", reviewed_at=newer))

    history = store.get_history("acme/widgets")

    assert history[0]["commit_sha"] == "new"
    assert history[1]["commit_sha"] == "old"


def test_get_history_respects_limit():
    conn = _FakeConnection()
    store = PostgresStore(conn=conn)
    for i in range(5):
        store.save_review(make_result(commit_sha=f"c{i}"))

    assert len(store.get_history("acme/widgets", limit=2)) == 2


def test_get_history_empty_for_unknown_repo():
    conn = _FakeConnection()
    store = PostgresStore(conn=conn)
    assert store.get_history("nonexistent/repo") == []


def test_critical_count_and_total_findings_are_persisted():
    conn = _FakeConnection()
    store = PostgresStore(conn=conn)
    findings = [
        Finding(file="a.py", line=1, category="runtime", severity=Severity.CRITICAL, message="x"),
        Finding(file="a.py", line=2, category="runtime", severity=Severity.WARNING, message="y"),
    ]
    store.save_review(make_result(findings=findings))

    history = store.get_history("acme/widgets")
    assert history[0]["critical_count"] == 1
    assert history[0]["total_findings"] == 2
