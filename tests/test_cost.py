"""Cost accounting: explicit inputs only, no fabricated prices."""

import json
import math

import pytest

from conftest import load_plugin, plugin_module

load_plugin()
cost = plugin_module("cost")


def _pricing(cached=None):
    return cost.Pricing(
        input_per_million=3.0,
        output_per_million=15.0,
        cached_input_per_million=cached,
    )


def test_known_arithmetic():
    usage = cost.Usage(input_tokens=1_000_000, output_tokens=500_000)
    assert cost.price(usage, _pricing()) == pytest.approx(10.5)


def test_cached_input_uses_its_own_rate():
    usage = cost.Usage(
        input_tokens=1_000_000, output_tokens=500_000, cached_input_tokens=400_000
    )
    assert cost.price(usage, _pricing(cached=0.3)) == pytest.approx(9.42)


def test_cached_input_without_a_rate_falls_back_to_the_input_rate():
    usage = cost.Usage(input_tokens=1_000_000, cached_input_tokens=400_000)
    report = cost.savings_report(
        baseline=usage, optimized=usage, pricing=_pricing(cached=None)
    )
    assert report["cost"]["cached_input_rate"] == "assumed at input rate"
    assert cost.price(usage, _pricing()) == pytest.approx(3.0)


def test_no_pricing_means_no_dollars_and_a_reason():
    usage = cost.Usage(input_tokens=1000, output_tokens=10)
    assert cost.price(usage, None) is None
    report = cost.savings_report(baseline=usage, optimized=usage)
    assert report["cost"]["basis"].startswith("unavailable:")
    assert report["cost"]["baseline_usd"] is None
    assert report["cost"]["optimized_usd"] is None
    assert report["cost"]["saved_usd"] is None
    assert report["pricing"] is None


def test_zero_usage_costs_zero_with_pricing_but_has_no_percentage():
    empty = cost.Usage()
    assert cost.price(empty, _pricing()) == 0.0
    report = cost.savings_report(
        baseline=empty, optimized=empty, pricing=_pricing()
    )
    assert report["percent_saved"] is None
    assert report["percent_saved_reason"] == "baseline has no tokens"
    assert report["cost"]["saved_usd"] == 0.0


def test_invalid_usage_is_rejected():
    with pytest.raises(ValueError):
        cost.Usage(input_tokens=-1)
    with pytest.raises(ValueError):
        cost.Usage(output_tokens=-5)
    with pytest.raises(ValueError):
        cost.Usage(input_tokens=10, cached_input_tokens=11)


def test_invalid_pricing_is_rejected():
    with pytest.raises(ValueError):
        cost.Pricing(input_per_million=-1.0, output_per_million=1.0)
    with pytest.raises(ValueError):
        cost.Pricing(input_per_million=math.nan, output_per_million=1.0)
    with pytest.raises(ValueError):
        cost.Pricing(input_per_million=1.0, output_per_million=math.inf)
    with pytest.raises(ValueError):
        cost.Pricing(
            input_per_million=1.0, output_per_million=1.0,
            cached_input_per_million=-0.5,
        )


def test_a_regression_reports_negative_savings():
    baseline = cost.Usage(input_tokens=1000, output_tokens=100)
    optimized = cost.Usage(input_tokens=1200, output_tokens=100)
    assert cost.token_savings(baseline, optimized) == -200


def test_percentage_of_a_real_baseline():
    baseline = cost.Usage(input_tokens=1000)
    optimized = cost.Usage(input_tokens=750)
    assert cost.percent_saved(baseline, optimized) == pytest.approx(25.0)


def test_report_keys_are_exactly_the_documented_set():
    usage = cost.Usage(input_tokens=10, output_tokens=5)
    report = cost.savings_report(
        baseline=usage, optimized=usage, pricing=_pricing(),
        baseline_bytes=100, optimized_bytes=60,
        baseline_ms=1.0, optimized_ms=2.5,
    )
    assert set(report) == {
        "baseline", "optimized", "saved_tokens", "percent_saved",
        "percent_saved_reason", "saved_bytes", "overhead_ms", "pricing", "cost",
    }
    assert set(report["cost"]) == {
        "baseline_usd", "optimized_usd", "saved_usd", "basis",
        "cached_input_rate",
    }
    assert set(report["baseline"]) == {"tokens", "bytes", "ms"}
    assert set(report["optimized"]) == {"tokens", "bytes", "ms"}


def test_optional_measures_stay_none_until_supplied():
    usage = cost.Usage(input_tokens=10)
    report = cost.savings_report(baseline=usage, optimized=usage)
    assert report["saved_bytes"] is None
    assert report["overhead_ms"] is None
    assert report["baseline"]["bytes"] is None
    report = cost.savings_report(
        baseline=usage, optimized=usage,
        baseline_bytes=100, optimized_bytes=60,
        baseline_ms=2.0, optimized_ms=5.0,
    )
    assert report["saved_bytes"] == 40
    assert report["overhead_ms"] == pytest.approx(3.0)


def test_report_is_json_serialisable():
    usage = cost.Usage(input_tokens=10)
    report = cost.savings_report(
        baseline=usage, optimized=usage, pricing=_pricing(cached=0.1),
        baseline_bytes=100, optimized_bytes=60,
        baseline_ms=1.0, optimized_ms=2.0,
    )
    assert json.loads(json.dumps(report))["cost"]["basis"] == "explicit pricing"


def test_no_dollar_number_survives_without_pricing():
    usage = cost.Usage(input_tokens=10)
    report = cost.savings_report(
        baseline=usage, optimized=usage,
        baseline_bytes=10, optimized_bytes=5,
    )
    cost_part = report["cost"]
    assert cost_part["baseline_usd"] is None
    assert cost_part["optimized_usd"] is None
    assert cost_part["saved_usd"] is None
    for key in ("baseline_usd", "optimized_usd", "saved_usd"):
        assert not isinstance(cost_part[key], (int, float))


# --------------------------------------------------------------- CLI surface

def test_cost_command_reports_tokens_without_pricing(capsys):
    cli = plugin_module("cli")
    code = cli.main([
        "cost",
        "--baseline-input", "100000", "--baseline-output", "5000",
        "--optimized-input", "45000", "--optimized-output", "5000",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "105000 -> 50000" in out
    assert "55000" in out
    assert "cost: unavailable: no pricing supplied" in out


def test_cost_command_uses_only_prices_the_caller_supplied(capsys):
    cli = plugin_module("cli")
    code = cli.main([
        "cost", "--baseline-input", "1000000", "--optimized-input", "400000",
        "--input-price", "3.0", "--output-price", "15.0", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["cost"]["basis"] == "explicit pricing"
    assert report["cost"]["baseline_usd"] == 3.0
    assert report["cost"]["optimized_usd"] == 1.2
    assert report["cost"]["saved_usd"] == 1.8


def test_cost_command_rejects_negative_usage(capsys):
    cli = plugin_module("cli")
    code = cli.main(["cost", "--baseline-input", "-1"])
    assert code == 2
    assert "negative" in capsys.readouterr().err
