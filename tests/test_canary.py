from src.core.canary import variant_for


def test_zero_percent_rollout_is_always_stable():
    for key in ("acme/widgets:abc123", "acme/other:def456", "x", "y", "z"):
        assert variant_for(key, 0) == "stable"


def test_hundred_percent_rollout_is_always_canary():
    for key in ("acme/widgets:abc123", "acme/other:def456", "x", "y", "z"):
        assert variant_for(key, 100) == "canary"


def test_same_key_always_resolves_to_the_same_variant():
    """A retried Celery task must not flip variants mid-flight, or a
    quality comparison between stable and canary becomes meaningless."""
    key = "acme/widgets:abc123"
    first = variant_for(key, 50)
    for _ in range(20):
        assert variant_for(key, 50) == first


def test_rollout_percentage_is_roughly_respected_over_many_keys():
    keys = [f"repo/name:{i}" for i in range(2000)]
    canary_count = sum(1 for k in keys if variant_for(k, 20) == "canary")
    fraction = canary_count / len(keys)
    assert 0.15 <= fraction <= 0.25  # 20% target, generous tolerance for hash variance


def test_out_of_range_percentages_are_clamped_not_raised():
    assert variant_for("k", -10) == "stable"
    assert variant_for("k", 250) == "canary"


def test_different_keys_can_land_in_different_variants_at_50_percent():
    variants = {variant_for(f"key-{i}", 50) for i in range(50)}
    assert variants == {"stable", "canary"}
