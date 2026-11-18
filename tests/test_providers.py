"""Provider usage metadata parsing."""

from __future__ import annotations

import json

import pytest

from conftest import plugin_module

providers = plugin_module("providers")
cost = plugin_module("cost")


def test_openai_chat_usage_is_mapped():
    payload = {
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
    }
    usage = providers.usage_from("openai", payload)
    assert usage == cost.Usage(1200, 300, 800)
    assert usage.total_tokens() == 1500


def test_openai_responses_shape_is_accepted():
    usage = providers.usage_from(
        "OpenAI",
        {"usage": {"input_tokens": 10, "output_tokens": 2}},
    )
    assert (usage.input_tokens, usage.output_tokens) == (10, 2)
    assert usage.cached_input_tokens == 0


def test_anthropic_folds_cache_tokens_into_input():
    payload = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 200,
        }
    }
    usage = providers.usage_from("anthropic", payload)
    assert usage.input_tokens == 1200
    assert usage.cached_input_tokens == 900
    assert usage.cache_write_tokens == 200
    assert usage.total_tokens() == 1250


def test_anthropic_cache_write_requires_a_cache_write_price():
    usage = providers.usage_from(
        "anthropic",
        {"usage": {"input_tokens": 0, "output_tokens": 10,
                   "cache_creation_input_tokens": 100}},
    )
    pricing_without_write = cost.Pricing(1.0, 2.0, 0.5)
    assert cost.price(usage, pricing_without_write) is None
    pricing_with_write = cost.Pricing(1.0, 2.0, 0.5, 1.25)
    assert cost.price(usage, pricing_with_write) == pytest.approx(
        (100 * 1.25 + 10 * 2.0) / 1_000_000, abs=1e-12)


def test_google_usage_metadata_is_mapped():
    payload = {
        "usageMetadata": {
            "promptTokenCount": 500,
            "candidatesTokenCount": 100,
            "cachedContentTokenCount": 400,
        }
    }
    usage = providers.usage_from("google", payload)
    assert usage == cost.Usage(500, 100, 400)


def test_deepseek_cache_hit_is_a_subset_of_prompt_tokens():
    payload = {
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_cache_hit_tokens": 600,
            "prompt_cache_miss_tokens": 400,
        }
    }
    usage = providers.usage_from("deepseek", payload)
    assert usage == cost.Usage(1000, 200, 600)


def test_deepseek_inconsistent_cache_hit_is_rejected():
    with pytest.raises(ValueError, match="exceeds prompt_tokens"):
        providers.usage_from(
            "deepseek",
            {"usage": {"prompt_tokens": 10, "completion_tokens": 1,
                       "prompt_cache_hit_tokens": 11}},
        )


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("openai", {}),
        ("openai", {"usage": {"prompt_tokens": 1}}),
        ("anthropic", {"usage": {"input_tokens": 1}}),
        ("google", {"usageMetadata": {"promptTokenCount": 1}}),
        ("google", {}),
        ("deepseek", {"usage": {"completion_tokens": 1}}),
        ("openai", []),
    ],
)
def test_missing_required_counts_raise(provider, payload):
    with pytest.raises(ValueError):
        providers.usage_from(provider, payload)


@pytest.mark.parametrize("value", [-1, 1.5, True, "10", None])
def test_invalid_counts_are_rejected(value):
    with pytest.raises(ValueError):
        providers.usage_from(
            "openai",
            {"usage": {"prompt_tokens": value, "completion_tokens": 1}},
        )


def test_unknown_provider_is_rejected_without_guessing():
    with pytest.raises(ValueError, match="unsupported provider"):
        providers.usage_from("cohere", {"usage": {}})
    with pytest.raises(ValueError, match="provider must be a string"):
        providers.usage_from(None, {})  # type: ignore[arg-type]


def test_parsed_usage_drives_savings_report():
    baseline = providers.usage_from(
        "openai", {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}})
    optimized = providers.usage_from(
        "openai", {"usage": {"prompt_tokens": 400, "completion_tokens": 100,
                             "prompt_tokens_details": {"cached_tokens": 300}}})
    report = cost.savings_report(
        baseline=baseline,
        optimized=optimized,
        pricing=cost.Pricing(2.0, 8.0, 0.5),
    )
    assert report["saved_tokens"] == 600
    assert report["cost"]["cached_input_rate"] == "explicit"
    assert report["cost"]["basis"] == "explicit pricing"


def test_cost_cli_ingests_a_provider_usage_file(tmp_path, capsys):
    payload = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 40,
        }
    }
    path = tmp_path / "anthropic.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    cli = plugin_module("cli")
    code = cli.main([
        "cost", "--original-input", "1000",
        "--usage-file", str(path), "--provider", "anthropic",
        "--input-price", "2", "--output-price", "8", "--json",
    ])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert code == 0
    # Anthropic reports cache reads outside input_tokens; the parser folds them in.
    assert report["tokens"]["billed_input"] == 140
    assert report["tokens"]["cached_input"] == 40
    assert report["tokens"]["original_input"] == 1000
