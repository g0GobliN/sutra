"""Tests for cache metadata, layer status and economics."""

import json

import pytest

import cache
from conftest import plugin_module


def test_metadata_round_trip():
    meta = cache.Metadata(
        input_fingerprint="abc", provider="openai", model="gpt-x",
        tool="Read", tool_version="3.2", config_version="cfg-7",
        dependencies={"src/app.py": "1:2:3"}, created=100.0, expires=200.0)
    again = cache.Metadata.from_dict(json.loads(json.dumps(meta.as_dict())))
    assert again == meta
    assert again.as_dict() == meta.as_dict()


def test_metadata_rejects_missing_fingerprint():
    with pytest.raises(ValueError):
        cache.Metadata(input_fingerprint="")


def test_metadata_rejects_unknown_validation_state():
    with pytest.raises(ValueError):
        cache.Metadata(input_fingerprint="abc", validation="maybe")


def test_metadata_rejects_non_string_identity_fields():
    with pytest.raises(TypeError):
        cache.Metadata(input_fingerprint="abc", provider=3)


def test_metadata_expiry_and_usability():
    meta = cache.Metadata(input_fingerprint="abc", created=100.0, expires=200.0)
    assert meta.expired(150.0) is False
    assert meta.expired(200.0) is True
    assert meta.usable(150.0) is True
    assert meta.usable(200.0) is False
    assert cache.Metadata(input_fingerprint="abc").expired(10**9) is False


def test_metadata_invalidated_or_unverified_is_not_usable():
    for state in ("invalidated", "expired", "unverified"):
        meta = cache.Metadata(input_fingerprint="abc", validation=state)
        assert meta.usable() is False


def test_cache_key_changes_with_every_invalidating_field():
    base = cache.cache_key("input")
    variants = [
        cache.cache_key("other"),
        cache.cache_key("input", provider="openai"),
        cache.cache_key("input", model="m"),
        cache.cache_key("input", tool="Read"),
        cache.cache_key("input", tool_version="2"),
        cache.cache_key("input", config_version="cfg-2"),
        cache.cache_key("input", dependencies={"a": "1"}),
        cache.cache_key("input", dependencies={"a": "2"}),
    ]
    assert all(value != base for value in variants)
    assert len(set(variants)) == len(variants)
    assert cache.cache_key("input", dependencies={"a": "1", "b": "2"}) \
        == cache.cache_key("input", dependencies={"b": "2", "a": "1"})
    assert len(base) == 64


def test_cache_key_requires_an_input():
    with pytest.raises(ValueError):
        cache.cache_key("")


def test_metadata_key_matches_cache_key():
    meta = cache.Metadata(input_fingerprint="input", provider="p", model="m",
                          dependencies={"a": "1"})
    assert meta.key() == cache.cache_key("input", provider="p", model="m",
                                         dependencies={"a": "1"})


def test_economics_break_even():
    economics = cache.Economics(write_cost=10.0, read_cost=1.0, miss_cost=5.0)
    assert economics.saving_per_reuse == 4.0
    assert economics.break_even() == 2.5
    assert economics.net(3) == 2.0
    assert economics.worth_it(3) is True
    assert economics.worth_it(2) is False


def test_economics_never_wins_when_a_hit_costs_as_much_as_a_miss():
    economics = cache.Economics(write_cost=10.0, read_cost=5.0, miss_cost=5.0)
    assert economics.break_even() is None
    assert economics.net(100) == -10.0
    assert economics.worth_it(100) is False


def test_economics_free_write_breaks_even_immediately():
    economics = cache.Economics(write_cost=0.0, read_cost=1.0, miss_cost=2.0)
    assert economics.break_even() == 0.0
    assert economics.worth_it(0) is False
    assert economics.worth_it(1) is True


def test_economics_rejects_negative_costs_and_reuses():
    with pytest.raises(ValueError):
        cache.Economics(write_cost=-1, read_cost=0, miss_cost=1)
    with pytest.raises(TypeError):
        cache.Economics(write_cost="1", read_cost=0, miss_cost=1)
    with pytest.raises(ValueError):
        cache.Economics(0, 0, 1).net(-1)


def test_layers_are_exactly_the_five_named_by_goals():
    entries = cache.layers()
    assert [entry["layer"] for entry in entries] == [
        "local-result", "context", "tool-result", "provider-prompt", "semantic"]
    statuses = {entry["layer"]: entry["status"] for entry in entries}
    assert statuses["local-result"] == "implemented"
    assert statuses["provider-prompt"] == "not-observable"
    assert statuses["semantic"] == "not-implemented"


def test_report_partitions_and_states_the_provider_caveat():
    payload = cache.report()
    assert payload["implemented"] == ["local-result"]
    assert payload["library_only"] == ["context", "tool-result"]
    assert set(payload["not_implemented"]) == {"provider-prompt", "semantic"}
    assert "never counted" in payload["provider_prompt_cache"]


def test_cli_reports_layers(tmp_path, capsys):
    del tmp_path
    cli = plugin_module("cli")
    assert cli.main(["cache"]) == 0
    out = capsys.readouterr().out
    assert "local-result" in out and "implemented" in out


def test_cli_json_and_economics(capsys):
    cli = plugin_module("cli")
    code = cli.main(["cache", "--json", "--write-cost", "10", "--read-cost",
                     "1", "--miss-cost", "5", "--reuses", "3"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["economics"]["break_even_reuses"] == 2.5
    assert payload["economics"]["net"] == 2.0
    assert payload["economics"]["worth_it"] is True
    assert len(payload["layers"]) == 5


def test_cli_rejects_partial_economics(capsys):
    cli = plugin_module("cli")
    assert cli.main(["cache", "--write-cost", "1"]) == 2
    assert "pass all of" in capsys.readouterr().err
