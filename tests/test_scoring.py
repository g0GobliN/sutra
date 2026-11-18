"""Tests for the SUTRA decision model (SROF, SEOF, SACE, SMESP)."""

from __future__ import annotations

import math

import pytest

from conftest import plugin_module

scoring = plugin_module("scoring")


def test_net_benefit_is_savings_minus_overhead():
    assert scoring.net_benefit(100.0, 20.0) == 80.0
    assert scoring.net_benefit(10.0, 25.0) == -15.0
    assert scoring.net_benefit(0.0, 0.0) == 0.0


def test_seof_with_unit_quality_and_no_risk_is_savings_over_1_plus_overhead():
    assert scoring.seof(100.0, 3.0) == pytest.approx(100.0 / 4.0)
    assert scoring.seof(100.0, 0.0) == pytest.approx(100.0)


def test_seof_scales_with_quality_and_falls_with_risk():
    base = scoring.seof(100.0, 9.0)
    assert scoring.seof(100.0, 9.0, quality=0.5) == pytest.approx(base / 2)
    assert scoring.seof(100.0, 9.0, risk=0.5) < base


def test_seof_and_priority_agree_with_the_old_search_ranking():
    """The loop's previous heuristic is SEOF with unit confidence."""
    for saved, elapsed in ((10, 0.0), (10, 4.0), (1, 0.5), (1000, 99.0)):
        assert scoring.seof(saved, elapsed) == pytest.approx(saved / (1.0 + elapsed))


def test_priority_rises_with_applicability_and_reuse_and_falls_with_overhead():
    assert scoring.priority(100.0, 1.0, applicable=0.5) < scoring.priority(100.0, 1.0)
    assert scoring.priority(100.0, 1.0, reuse=2.0) > scoring.priority(100.0, 1.0)
    assert scoring.priority(100.0, 3.0) < scoring.priority(100.0, 1.0)


def test_a_tiny_probability_keeps_a_non_zero_priority():
    """SMESP forbids using a tiny probability as a reason to stop."""
    assert scoring.priority(100.0, 0.0, applicable=0.000001) > 0.0


def test_sace_chooses_the_cheapest_admissible_candidate():
    cheap_risky = scoring.Candidate("cheap-risky", cost=1.0, quality=0.4, risk=0.9)
    safe = scoring.Candidate("safe", cost=3.0, quality=0.99, risk=0.01)
    selection = scoring.sace_select(
        [cheap_risky, safe], min_quality=0.9, max_risk=0.1)
    assert selection is not None
    assert selection.chosen.name == "safe"
    assert [item.name for item in selection.rejected] == ["cheap-risky"]


def test_sace_enforces_latency_and_returns_none_when_nothing_is_admissible():
    slow = scoring.Candidate("slow", cost=1.0, latency_ms=250.0)
    assert scoring.sace_select([slow], max_latency_ms=100.0) is None
    assert scoring.sace_select([], min_quality=0.0) is None


def test_sace_tie_breaks_on_name_for_determinism():
    first = scoring.Candidate("b", cost=2.0)
    second = scoring.Candidate("a", cost=2.0)
    selection = scoring.sace_select([first, second])
    assert selection is not None
    assert selection.chosen.name == "a"


def test_sace_reports_rejected_candidates_without_losing_the_chosen_one():
    items = [scoring.Candidate("a", cost=1.0), scoring.Candidate("b", cost=2.0),
             scoring.Candidate("c", cost=0.5, risk=0.9)]
    selection = scoring.sace_select(items, max_risk=0.2)
    assert selection is not None
    assert selection.chosen.name == "a"
    assert {item.name for item in selection.rejected} == {"b", "c"}


def test_pareto_front_removes_dominated_candidates_only():
    dominated = scoring.Candidate("dominated", cost=5.0, quality=0.5)
    better = scoring.Candidate("better", cost=4.0, quality=0.9)
    tradeoff = scoring.Candidate("tradeoff", cost=1.0, quality=0.1)
    front = scoring.pareto_front([dominated, better, tradeoff])
    assert [item.name for item in front] == ["better", "tradeoff"]


def test_equal_candidates_do_not_dominate_each_other():
    left = scoring.Candidate("left", cost=1.0, quality=0.5)
    right = scoring.Candidate("right", cost=1.0, quality=0.5)
    assert not scoring.dominates(left, right)
    assert not scoring.dominates(right, left)
    assert len(scoring.pareto_front([left, right])) == 2


@pytest.mark.parametrize("field,value", [
    ("savings", -1.0),
    ("overhead", -1.0),
    ("latency_ms", -1.0),
    ("cost", -1.0),
    ("reuse", -1.0),
    ("quality", 1.5),
    ("risk", -0.1),
    ("applicable", 2.0),
])
def test_candidate_rejects_invalid_inputs(field, value):
    with pytest.raises(ValueError):
        scoring.Candidate("bad", **{field: value})


def test_candidate_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        scoring.Candidate("bad", cost=math.inf)
    with pytest.raises(ValueError):
        scoring.seof(math.nan, 0.0)


def test_candidate_rejects_empty_name():
    with pytest.raises(ValueError):
        scoring.Candidate("")


def test_candidate_as_dict_is_json_ready():
    item = scoring.Candidate("candidate", savings=10.0, overhead=1.0)
    data = item.as_dict()
    assert data["name"] == "candidate"
    assert data["savings"] == 10.0
    assert set(data) == {
        "name", "savings", "overhead", "quality", "risk", "latency_ms",
        "cost", "applicable", "reuse",
    }
