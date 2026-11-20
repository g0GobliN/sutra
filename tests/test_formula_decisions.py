"""Every formula must change a decision, not just produce a number.

The other formula tests check arithmetic. These check the thing the original
specification actually asks for: feed a different input, get a different
*decision* out of the production path. A formula whose score nothing reads
would fail here even though its unit tests pass.

Each test names the decision it pins, so a regression points at the call site
that stopped consulting the formula.
"""

from __future__ import annotations

from conftest import plugin_module

scoring = plugin_module("scoring")

preserve = plugin_module("preserve")
optimize = plugin_module("optimize")
orchestrator = plugin_module("orchestrator")
context = plugin_module("context")
cost = plugin_module("cost")
cache = plugin_module("cache")


def _policy():
    return orchestrator.policy_for(plugin_module("config").resolve())


# -- SPC: preservation rejects a candidate ----------------------------------


def test_spc_rejects_a_transform_that_drops_a_protected_token():
    """The decision is 'reject': the original wins even though bytes were saved."""
    original = "Run pytest -q --maxfail 3 to reproduce the failure."
    candidate = "Run pytest to reproduce the failure."  # drops a number and a flag

    ok, reasons = preserve.preservation_ok(original, candidate)

    assert ok is False
    assert reasons, "a rejection must name what it dropped"

    strategy = optimize.Strategy(
        "destroyer", lambda _text: candidate, "deliberately unsafe")
    report = optimize.optimize(original, strategies=[strategy], max_passes=1,
                               min_saved_bytes=1)

    assert report.text == original, "a rejected candidate must never be emitted"
    assert not any(item.accepted for item in report.evaluations)
    assert report.saved_bytes == 0


def test_spc_admits_a_transform_that_keeps_every_protected_token():
    original = "In order to reproduce, run pytest -q --maxfail 3 first."
    safe = optimize.Strategy(
        "phrases", lambda text: text.replace("In order to", "To"))

    report = optimize.optimize(original, strategies=[safe], max_passes=1,
                               min_saved_bytes=1)

    assert report.text == "To reproduce, run pytest -q --maxfail 3 first."
    assert any(item.accepted for item in report.evaluations)


# -- SROF: net benefit decides admission ------------------------------------


def _plan_result(name: str, saved: int, original: int = 100_000):
    return orchestrator.PlanResult(
        plan=name, text="x" * (original - saved), saved_bytes=saved,
        original_bytes=original, elapsed_ms=1.0, applied=(name,),
        accepted=1, rejected=0)


def test_srof_nominal_overhead_admits_any_measured_saving():
    """``nominal`` is the deterministic default: a real saving is admitted.

    Host load must not change which plan wins, so the default charges a
    documented constant instead of wall-clock time.
    """
    chosen, decision = orchestrator._decide(
        [_plan_result("tiny", 1)], _policy(), 100_000)

    assert chosen is not None and chosen.plan == "tiny"
    assert decision["trace"][0]["overhead_source"] == "nominal"


def test_srof_measured_overhead_rejects_a_sliver_that_spends_the_budget():
    """With ``measured``, 1 byte saved out of 100 kB for 50 ms of a 50 ms
    budget is a loss: the plan consumed its whole allowance."""
    slow = orchestrator.PlanResult(
        plan="tiny", text="x" * 99_999, saved_bytes=1, original_bytes=100_000,
        elapsed_ms=_policy().budget_ms, applied=("tiny",), accepted=1,
        rejected=0)

    chosen, decision = orchestrator._decide(
        [slow], _policy(), 100_000, overhead_source="measured")

    assert chosen is None
    assert decision["trace"][0]["admitted"] is False
    assert decision["trace"][0]["overhead"] == 1.0


def test_srof_admits_the_same_shape_of_plan_when_the_saving_is_large():
    chosen, decision = orchestrator._decide(
        [_plan_result("big", 40_000)], _policy(), 100_000)

    assert chosen is not None and chosen.plan == "big"
    assert decision["sace"]["chosen"] == "big"
    assert decision["trace"][0]["sroF_benefit"] > 0


# -- SACE: cost decides which admissible plan wins --------------------------


def test_sace_prefers_the_cheaper_plan_when_both_are_admissible():
    chosen, decision = orchestrator._decide(
        [_plan_result("small", 5_000), _plan_result("large", 60_000)],
        _policy(), 100_000)

    assert chosen.plan == "large"
    assert decision["sace"]["rejected"] == ["small"]


def test_sace_excludes_a_plan_that_failed_even_if_it_reports_savings():
    broken = orchestrator.PlanResult(
        plan="broken", text="", saved_bytes=90_000, original_bytes=100_000,
        elapsed_ms=1.0, applied=(), accepted=0, rejected=0,
        error="RuntimeError: boom")

    chosen, decision = orchestrator._decide(
        [broken, _plan_result("sound", 10_000)], _policy(), 100_000)

    assert chosen.plan == "sound"
    assert decision["trace"][0]["reason"] == "RuntimeError: boom"


# -- Pareto: the front names what a caller may still consider ---------------


def test_pareto_drops_a_plan_dominated_on_every_dimension():
    """Ranking cost is derived from the saving, so the smaller save is
    dominated; the front still reports what survived."""
    _chosen, decision = orchestrator._decide(
        [_plan_result("a", 20_000), _plan_result("b", 30_000)],
        _policy(), 100_000)

    assert decision["pareto"] == ["b"]
    assert decision["sace"]["chosen"] == "b"


def test_pareto_keeps_a_tradeoff_when_quality_differs():
    """A plan that saves less but scores better on quality stays on the front."""
    a = scoring.Candidate(name="a", savings=0.20, overhead=0.0, quality=1.0,
                          risk=0.0, latency_ms=1.0, cost=0.80)
    b = scoring.Candidate(name="b", savings=0.40, overhead=0.0, quality=0.5,
                          risk=0.2, latency_ms=1.0, cost=0.60)

    assert {item.name for item in scoring.pareto_front([a, b])} == {"a", "b"}


def test_pareto_does_not_report_an_inadmissible_plan():
    _chosen, decision = orchestrator._decide(
        [_plan_result("tiny", 1), _plan_result("b", 30_000)],
        _policy(), 100_000)

    assert "tiny" not in decision["pareto"]


# -- SMESP: the estimate changes the order plans are tried in ---------------


def test_smesp_orders_the_plan_that_pays_off_first():
    verbose = (
        "Note that the vast majority of the changes are required in order to "
        "improve performance. "
    ) * 40
    plans = orchestrator._plans(_policy())

    ordered = orchestrator._ordered_plans(verbose, plans, 50.0)

    assert len(ordered) == len(plans)
    assert ordered[0].name in {"caveman", "format+caveman"}, (
        "the wording plan must be tried before a plan that finds nothing")


# -- V_i and precedence selection: the selection changes with the inputs ----


def _item(name, *, relevance, tokens, mandatory=False, dependency=()):
    return context.Item(
        id=name, content=name, token_cost=tokens, relevance=relevance,
        importance=relevance, mandatory=mandatory, dependency=tuple(dependency))


def test_context_value_changes_which_items_survive_the_budget():
    items = [_item("high", relevance=0.9, tokens=6),
             _item("low", relevance=0.1, tokens=6)]

    selection = context.select(items, budget=6)

    assert selection.selected == ("high",)
    assert "low" in [item_id for item_id, _reason in selection.dropped]


def test_reordering_the_same_items_does_not_change_the_selection():
    items = [_item("high", relevance=0.9, tokens=6),
             _item("low", relevance=0.1, tokens=6)]

    forwards = context.select(items, budget=6)
    backwards = context.select(list(reversed(items)), budget=6)

    assert forwards.selected == backwards.selected


def test_a_mandatory_item_wins_over_a_more_relevant_one():
    items = [_item("must", relevance=0.0, tokens=6, mandatory=True),
             _item("wanted", relevance=0.99, tokens=6)]

    selection = context.select(items, budget=6)

    assert selection.selected == ("must",)


def test_a_dependency_is_pulled_in_with_the_item_that_needs_it():
    items = [_item("base", relevance=0.0, tokens=4),
             _item("user", relevance=0.9, tokens=4, dependency=("base",))]

    selection = context.select(items, budget=8)

    assert set(selection.selected) == {"base", "user"}


def test_an_impossible_budget_reports_a_conflict_instead_of_dropping_mandatory():
    items = [_item("must", relevance=0.5, tokens=10, mandatory=True)]

    selection = context.select(items, budget=3)

    assert selection.feasible is False
    assert selection.conflict, "an infeasible budget must say so"
    assert selection.selected == ("must",), "mandatory context is never dropped"


# -- total cost: the decision is whether the run saved money -----------------


def test_total_cost_flips_from_saving_to_loss_when_overhead_is_added():
    pricing = cost.Pricing(input_per_million=3.0, output_per_million=15.0)
    cheap = cost.Accounting(original_input_tokens=100_000,
                            billed_input_tokens=60_000, output_tokens=1_000)
    expensive = cost.Accounting(original_input_tokens=100_000,
                                billed_input_tokens=60_000, output_tokens=1_000,
                                extra_cost=1.0)

    assert cheap.net_savings(pricing) > 0
    assert expensive.net_savings(pricing) < 0, (
        "SUTRA's own cost must be able to turn a saving into a loss")


def test_provider_cache_reads_change_the_measured_saving():
    pricing = cost.Pricing(input_per_million=3.0, output_per_million=15.0,
                           cached_input_per_million=0.3)
    uncached = cost.Accounting(original_input_tokens=100_000,
                               billed_input_tokens=60_000, output_tokens=0)
    cached = cost.Accounting(original_input_tokens=100_000,
                             billed_input_tokens=60_000,
                             cached_input_tokens=60_000, output_tokens=0)

    assert cached.net_savings(pricing) > uncached.net_savings(pricing)


# -- cache economics: the advice changes with the caller's numbers ----------


def test_cache_advice_flips_at_the_break_even_point(monkeypatch):
    monkeypatch.setenv("SUTRA_MODE", "balanced")
    economics = cache.Economics(write_cost=10.0, read_cost=1.0, miss_cost=5.0)
    break_even = economics.break_even()

    below = orchestrator.cache_advice(economics, break_even - 1)
    above = orchestrator.cache_advice(economics, break_even + 1)

    assert below["worth_reusing"] is False
    assert above["worth_reusing"] is True


def test_cache_advice_is_withheld_in_off_mode(monkeypatch):
    monkeypatch.setenv("SUTRA_MODE", "off")
    economics = cache.Economics(write_cost=1.0, read_cost=0.1, miss_cost=5.0)

    assert orchestrator.cache_advice(economics, 100) is None
