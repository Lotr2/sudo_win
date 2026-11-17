"""The gate recommends STOP on weak evidence with the same pre-set thresholds for every variant."""
import gates


def test_phase2_stops_on_few_sources():
    assert gates.phase2(independent_sources=2, assumption_ratio=0.2, demand_result=None).outcome == "stop"


def test_phase2_stops_on_assumptions():
    assert gates.phase2(independent_sources=5, assumption_ratio=0.8, demand_result=None).outcome == "stop"


def test_phase2_stops_when_demand_test_misses_target():
    r = gates.phase2(independent_sources=5, assumption_ratio=0.1, demand_result={"target": 100, "actual": 12})
    assert r.outcome == "stop"


def test_phase2_go_is_labelled_desk_only_without_demand_data():
    r = gates.phase2(independent_sources=4, assumption_ratio=0.2, demand_result=None)
    assert r.outcome == "go" and any("Desk evidence only" in x for x in r.reasons)


def test_phase1_stops_on_excluded_category():
    assert gates.phase1(excluded=["financial_or_crypto"], verified_pain=3).outcome == "stop"


def test_phase4_price_outside_range_repeats():
    assert gates.phase4(price=500, competitor_prices=[10, 20], policies_present=True).outcome == "repeat"


def test_phase6_only_mocked_data_recommends_repeat():
    assert gates.phase6(real_customer_evidence=False).outcome == "repeat"
