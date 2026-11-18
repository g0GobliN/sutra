"""Tests for full per-session accounting."""

import json

import pytest

import cost
from conftest import plugin_module

PRICING = cost.Pricing(input_per_million=2.0, output_per_million=8.0)


def _accounting(**overrides):
    values = dict(original_input_tokens=100_000, billed_input_tokens=80_000,
                  cached_input_tokens=20_000, output_tokens=1_000,
                  model_calls=1, tool_calls=3, processing_ms=10.0,
                  validation_ms=5.0, tokenizer="o200k")
    values.update(overrides)
    return cost.Accounting(**values)


def test_usage_and_baseline_differ_only_in_input():
    accounting = _accounting()
    assert accounting.usage() == cost.Usage(80_000, 1_000, 20_000)
    assert accounting.baseline() == cost.Usage(100_000, 1_000, 0)


def test_actual_cost_includes_sutra_overhead():
    accounting = _accounting(extra_cost=0.001)
    # 60000*2 + 20000*2 (cached assumed at input rate) + 1000*8 = 0.168
    assert accounting.actual_cost(PRICING) == 0.169
    assert accounting.baseline_cost(PRICING) == 0.208
    assert accounting.net_savings(PRICING) == 0.039


def test_cost_is_none_without_pricing():
    accounting = _accounting()
    assert accounting.actual_cost(None) is None
    assert accounting.baseline_cost(None) is None
    assert accounting.net_savings(None) is None
    assert accounting.efficiency(None) is None


def test_cached_rate_used_when_supplied():
    with_cache_rate = cost.Pricing(2.0, 8.0, cached_input_per_million=0.5)
    accounting = _accounting()
    # 60000*2 + 20000*0.5 + 1000*8, over one million, = 0.138
    assert accounting.actual_cost(with_cache_rate) == 0.138


def test_efficiency_needs_tasks_and_a_positive_cost():
    assert _accounting().efficiency(PRICING) is None
    assert _accounting(useful_tasks=2).efficiency(None) is None
    assert _accounting(useful_tasks=2).efficiency(PRICING) == round(2 / 0.168, 12)


def test_validation_rejects_bad_accounting():
    with pytest.raises(ValueError):
        _accounting(original_input_tokens=-1)
    with pytest.raises(ValueError):
        _accounting(cached_input_tokens=90_000)
    with pytest.raises(ValueError):
        _accounting(processing_ms=-0.5)
    with pytest.raises(ValueError):
        _accounting(model_calls="3")
    with pytest.raises(ValueError):
        _accounting(useful_tasks=-2)


def test_report_has_every_part_14_field():
    report = cost.accounting_report(
        _accounting(extra_cost=0.001, useful_tasks=4,
                    provider_latency_ms=900.0, total_latency_ms=950.0),
        PRICING)
    assert report["tokens"]["original_total"] == 101_000
    assert report["tokens"]["optimized_total"] == 81_000
    assert report["tokens"]["percent_saved"] == round(20_000 / 101_000 * 100, 6)
    assert report["tokens"]["tokenizer"] == "o200k"
    assert report["calls"] == {"models": 1, "tools": 3}
    assert report["latency"] == {
        "processing_ms": 10.0, "validation_ms": 5.0, "overhead_ms": 15.0,
        "provider_ms": 900.0, "total_ms": 950.0}
    assert report["cost"]["basis"] == "explicit pricing"
    assert report["cost"]["net_savings"] == 0.039
    assert report["cost"]["estimated_baseline"] is False
    assert report["quality"]["useful_tasks"] == 4
    assert report["quality"]["efficiency"] == round(4 / 0.169, 12)
    assert report["accounting"]["overhead_ms"] == 15.0


def test_report_without_pricing_says_so():
    report = cost.accounting_report(_accounting())
    assert report["cost"]["basis"] == "unavailable: no pricing supplied"
    assert report["cost"]["actual"] is None
    assert report["cost"]["net_savings"] is None


def test_report_marks_an_estimated_baseline():
    report = cost.accounting_report(_accounting(baseline_estimated=True), PRICING)
    assert report["cost"]["estimated_baseline"] is True


def test_report_warns_when_the_tokenizer_is_not_named():
    report = cost.accounting_report(_accounting(tokenizer=""))
    assert "do not compare" in report["tokens"]["reduction_basis"]


def test_cli_full_accounting_json(capsys):
    cli = plugin_module("cli")
    code = cli.main([
        "cost", "--original-input", "100000", "--billed-input", "80000",
        "--cached", "0", "--output", "1000", "--model-calls", "1",
        "--tool-calls", "3", "--processing-ms", "10", "--validation-ms", "5",
        "--input-price", "2", "--output-price", "8", "--extra-cost", "0.001",
        "--useful-tasks", "4", "--tokenizer", "o200k", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["tokens"]["saved"] == 20_000
    assert payload["cost"]["net_savings"] == 0.039
    assert payload["quality"]["efficiency"] == round(4 / 0.169, 12)


def test_cli_rejects_impossible_cached_input(capsys):
    cli = plugin_module("cli")
    code = cli.main(["cost", "--original-input", "10", "--billed-input", "5",
                     "--cached", "6"])
    assert code == 2
    assert "cannot exceed" in capsys.readouterr().err
