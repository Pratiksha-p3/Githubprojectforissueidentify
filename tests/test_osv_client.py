from src.integrations import osv_client


class _FakeResponse:
    def __init__(self, json_data: dict):
        self._json_data = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json_data


def test_ecosystem_for_file_maps_known_extensions():
    assert osv_client.ecosystem_for_file("app.py") == "PyPI"
    assert osv_client.ecosystem_for_file("index.js") == "npm"
    assert osv_client.ecosystem_for_file("main.go") == "Go"


def test_ecosystem_for_file_returns_none_for_unknown_extension():
    assert osv_client.ecosystem_for_file("README.md") is None


def test_lookup_vulnerabilities_parses_results_with_numeric_score(monkeypatch):
    payload = {
        "vulns": [
            {
                "id": "GHSA-xxxx-yyyy-zzzz",
                "summary": "Denial of service via crafted input",
                "severity": [{"type": "CVSS_V3", "score": "7.5"}],
            }
        ]
    }
    monkeypatch.setattr(osv_client.requests, "post", lambda *a, **k: _FakeResponse(payload))

    results = osv_client.lookup_vulnerabilities("some-package", "PyPI")

    assert len(results) == 1
    assert results[0]["cve_id"] == "GHSA-xxxx-yyyy-zzzz"
    assert results[0]["cvss_score"] == 7.5
    assert "Denial of service" in results[0]["description"]


def test_cvss_vector_string_is_not_mistaken_for_a_numeric_score(monkeypatch):
    """Real bug found via live testing against the actual OSV API: most
    real entries carry a CVSS *vector string* under "score"
    (e.g. "CVSS:3.1/AV:N/AC:H/...") rather than a plain number — trying
    to use it as a float used to crash cve_enrichment's severity
    comparison outright."""
    payload = {
        "vulns": [
            {
                "id": "GHSA-9hjg-9r4m-mvj7",
                "summary": "Requests vulnerable to .netrc credentials leak",
                "severity": [
                    {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:N/A:N"}
                ],
                "database_specific": {"severity": "MODERATE"},
            }
        ]
    }
    monkeypatch.setattr(osv_client.requests, "post", lambda *a, **k: _FakeResponse(payload))

    results = osv_client.lookup_vulnerabilities("requests", "PyPI")

    assert results[0]["cvss_score"] is None
    assert results[0]["qualitative_severity"] == "MODERATE"


def test_lookup_vulnerabilities_returns_empty_list_on_no_matches(monkeypatch):
    monkeypatch.setattr(osv_client.requests, "post", lambda *a, **k: _FakeResponse({"vulns": []}))
    assert osv_client.lookup_vulnerabilities("safe-package", "PyPI") == []


def test_lookup_vulnerabilities_returns_empty_list_on_request_failure(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(osv_client.requests, "post", _raise)
    assert osv_client.lookup_vulnerabilities("some-package", "PyPI") == []
