from src.storage.decision_log import DecisionLog


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
        if normalized.startswith("SELECT") and "GROUP BY" in normalized:
            by_source: dict[str, list[str]] = {}
            for row in self._conn.rows:
                by_source.setdefault(row[4], []).append(row[6])
            self._last_result = [
                (source, sum(1 for d in decisions if d == "accepted"), len(decisions))
                for source, decisions in by_source.items()
            ]
            return
        if normalized.startswith("SELECT"):
            finding_source, limit = params
            matching = [r for r in self._conn.rows if r[4] == finding_source]
            matching.sort(key=lambda r: r[8], reverse=True)
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


def test_record_decision_persists_and_commits():
    conn = _FakeConnection()
    log = DecisionLog(conn=conn)

    log.record_decision(
        repo="acme/widgets", commit_sha="abc", file="app.py", line=5,
        finding_source="division_guard_checker", confidence="medium",
        decision="accepted", actor="alice",
    )

    assert len(conn.rows) == 1
    assert conn.rows[0][4] == "division_guard_checker"
    assert conn.rows[0][6] == "accepted"
    assert conn.commits >= 1


def test_history_for_source_returns_matching_records():
    conn = _FakeConnection()
    log = DecisionLog(conn=conn)
    log.record_decision(
        repo="r", commit_sha="c1", file="a.py", line=1, finding_source="checker_a",
        confidence="high", decision="accepted", actor="alice",
    )
    log.record_decision(
        repo="r", commit_sha="c2", file="a.py", line=2, finding_source="checker_b",
        confidence="high", decision="rejected", actor="alice",
    )

    history = log.history_for_source("checker_a")

    assert len(history) == 1
    assert history[0]["finding_source"] == "checker_a"
    assert history[0]["decision"] == "accepted"


def test_history_for_source_empty_for_unknown_source():
    conn = _FakeConnection()
    log = DecisionLog(conn=conn)
    assert log.history_for_source("nonexistent_checker") == []


def test_acceptance_rates_computed_correctly():
    conn = _FakeConnection()
    log = DecisionLog(conn=conn)
    for decision in ("accepted", "accepted", "rejected"):
        log.record_decision(
            repo="r", commit_sha="c", file="a.py", line=1, finding_source="checker_a",
            confidence="high", decision=decision, actor="alice",
        )

    rates = log.acceptance_rates()

    assert len(rates) == 1
    assert rates[0]["finding_source"] == "checker_a"
    assert rates[0]["accepted"] == 2
    assert rates[0]["total"] == 3
    assert rates[0]["acceptance_rate"] == round(2 / 3, 3)
