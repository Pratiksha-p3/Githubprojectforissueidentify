from datetime import UTC, datetime

from src.core.audit_log import AuditLog


class _FakeCursor:
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
            matching = [r for r in self._conn.rows if r[2] == repo]
            matching.sort(key=lambda r: r[5], reverse=True)
            self._last_result = matching[:limit]

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


def test_record_persists_and_commits():
    conn = _FakeConnection()
    log = AuditLog(conn=conn)

    log.record(
        actor="system", action="review", repo="acme/widgets", commit_sha="abc", detail="ok"
    )

    assert len(conn.rows) == 1
    assert conn.rows[0][0] == "system"
    assert conn.rows[0][1] == "review"
    assert conn.commits >= 1


def test_history_for_repo_returns_matching_records():
    conn = _FakeConnection()
    log = AuditLog(conn=conn)
    log.record(actor="system", action="review", repo="acme/widgets", commit_sha="c1")
    log.record(actor="system", action="review", repo="other/repo", commit_sha="c2")

    history = log.history_for_repo("acme/widgets")

    assert len(history) == 1
    assert history[0]["commit_sha"] == "c1"


def test_history_for_repo_orders_most_recent_first():
    conn = _FakeConnection()
    log = AuditLog(conn=conn)
    older = datetime(2024, 1, 1, tzinfo=UTC)
    newer = datetime(2024, 6, 1, tzinfo=UTC)

    conn.rows.append(("system", "review", "acme/widgets", "old", "", older))
    conn.rows.append(("system", "review", "acme/widgets", "new", "", newer))

    history = log.history_for_repo("acme/widgets")

    assert history[0]["commit_sha"] == "new"
    assert history[1]["commit_sha"] == "old"


def test_history_for_repo_respects_limit():
    conn = _FakeConnection()
    log = AuditLog(conn=conn)
    for i in range(5):
        log.record(actor="system", action="review", repo="acme/widgets", commit_sha=f"c{i}")

    assert len(log.history_for_repo("acme/widgets", limit=2)) == 2


def test_history_for_repo_empty_for_unknown_repo():
    conn = _FakeConnection()
    log = AuditLog(conn=conn)
    assert log.history_for_repo("nonexistent/repo") == []
