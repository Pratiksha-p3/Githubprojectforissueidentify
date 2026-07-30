from src.integrations import nvd_client


class _FakeResponse:
    def __init__(self, json_data: dict):
        self._json_data = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json_data


def make_nvd_payload(cve_id: str, score: float, description: str) -> dict:
    return {
        "vulnerabilities": [
            {
                "cve": {
                    "id": cve_id,
                    "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": score}}]},
                    "descriptions": [{"lang": "en", "value": description}],
                }
            }
        ]
    }


def test_lookup_cves_parses_results(monkeypatch):
    payload = make_nvd_payload("CVE-2024-1234", 9.8, "Critical remote code execution")
    monkeypatch.setattr(nvd_client.requests, "get", lambda *a, **k: _FakeResponse(payload))

    results = nvd_client.lookup_cves("some-package")

    assert len(results) == 1
    assert results[0]["cve_id"] == "CVE-2024-1234"
    assert results[0]["cvss_score"] == 9.8
    assert "remote code execution" in results[0]["description"]


def test_lookup_cves_returns_empty_list_on_no_matches(monkeypatch):
    monkeypatch.setattr(
        nvd_client.requests, "get", lambda *a, **k: _FakeResponse({"vulnerabilities": []})
    )
    assert nvd_client.lookup_cves("safe-package") == []


def test_lookup_cves_returns_empty_list_on_request_failure(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(nvd_client.requests, "get", _raise)
    assert nvd_client.lookup_cves("some-package") == []


def test_is_retryable_matches_transient_errors_only():
    assert nvd_client._is_retryable(Exception("429 too many requests")) is True
    assert nvd_client._is_retryable(Exception("connection reset")) is True
    assert nvd_client._is_retryable(Exception("404 not found")) is False
