import json

from src.dashboard.export import export_json, export_pdf


def test_export_json_produces_valid_json_with_expected_keys():
    output = export_json(
        "acme/widgets",
        [{"commit_sha": "c1"}],
        {"score": 10.0, "trend": "stable", "reviews_considered": 1},
    )
    data = json.loads(output)
    assert data["repo"] == "acme/widgets"
    assert data["risk_score"]["score"] == 10.0
    assert data["history"][0]["commit_sha"] == "c1"


def test_export_pdf_produces_valid_pdf_bytes():
    history = [
        {
            "reviewed_at": "2024-01-01",
            "commit_sha": "abcdef1234",
            "status": "completed",
            "critical_count": 0,
            "total_findings": 0,
        }
    ]
    pdf_bytes = export_pdf(
        "acme/widgets", history, {"score": 0.0, "trend": "stable", "reviews_considered": 1}
    )
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 100


def test_export_pdf_handles_many_rows_with_pagination():
    history = [
        {
            "reviewed_at": f"2024-01-{i:02d}",
            "commit_sha": f"commit{i}",
            "status": "completed",
            "critical_count": 0,
            "total_findings": 0,
        }
        for i in range(1, 30)
    ]
    pdf_bytes = export_pdf(
        "acme/widgets", history, {"score": 0.0, "trend": "stable", "reviews_considered": 29}
    )
    assert pdf_bytes.startswith(b"%PDF")


def test_export_pdf_handles_empty_history():
    pdf_bytes = export_pdf(
        "acme/widgets", [], {"score": 0.0, "trend": "stable", "reviews_considered": 0}
    )
    assert pdf_bytes.startswith(b"%PDF")
