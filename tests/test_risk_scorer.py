from src.core.risk_scorer import compute_risk_score


def make_row(status="completed", critical=0, total=0, repo="acme/widgets") -> dict:
    return {
        "repo": repo,
        "commit_sha": "x",
        "status": status,
        "critical_count": critical,
        "total_findings": total,
        "summary": "",
        "reviewed_at": "2024-01-01",
    }


def test_empty_history_is_zero_risk():
    risk = compute_risk_score([])
    assert risk.score == 0.0
    assert risk.trend == "stable"
    assert risk.reviews_considered == 0


def test_clean_reviews_produce_zero_risk():
    history = [make_row(critical=0, total=0) for _ in range(5)]
    risk = compute_risk_score(history)
    assert risk.score == 0.0


def test_critical_findings_increase_risk():
    history = [make_row(critical=3, total=3)]
    risk = compute_risk_score(history)
    assert risk.score > 50


def test_degraded_status_contributes_risk_even_with_no_findings():
    """An incomplete review is 'unknown', not 'safe' -- unknown must
    never score as zero risk just because no findings were reported."""
    history = [make_row(status="degraded", critical=0, total=0)]
    risk = compute_risk_score(history)
    assert risk.score > 0


def test_improving_trend_when_recent_reviews_are_cleaner():
    history = [
        make_row(critical=0, total=0),  # most recent
        make_row(critical=0, total=0),
        make_row(critical=5, total=5),
        make_row(critical=5, total=5),  # oldest
    ]
    assert compute_risk_score(history).trend == "improving"


def test_worsening_trend_when_recent_reviews_are_worse():
    history = [
        make_row(critical=5, total=5),  # most recent
        make_row(critical=5, total=5),
        make_row(critical=0, total=0),
        make_row(critical=0, total=0),  # oldest
    ]
    assert compute_risk_score(history).trend == "worsening"


def test_stable_trend_for_consistent_history():
    history = [make_row(critical=1, total=1) for _ in range(4)]
    assert compute_risk_score(history).trend == "stable"


def test_single_review_history_is_always_stable_trend():
    assert compute_risk_score([make_row(critical=5, total=5)]).trend == "stable"


def test_risk_score_capped_at_100():
    history = [make_row(critical=20, total=20)]
    assert compute_risk_score(history).score <= 100.0


def test_reviews_considered_matches_history_length():
    history = [make_row() for _ in range(7)]
    assert compute_risk_score(history).reviews_considered == 7


def test_repo_name_taken_from_most_recent_row():
    history = [make_row(repo="acme/widgets")]
    assert compute_risk_score(history).repo == "acme/widgets"
