"""One configuration owner: every consumer reads the same resolved values.

These tests resolve the configuration once with ``sutra.configuration()``, then
assert that each component's view is derived from *that* object — not from its
own parse of the environment.
"""

from __future__ import annotations

import json
import os

import pytest

_DEFAULTS = {
    "enabled": True,
    "max_bytes": 24_576,
    "head_bytes": 12_288,
    "tail_bytes": 6_144,
    "stats_max_bytes": 5 * 1024 * 1024,
    "store_enabled": True,
    "store_ttl_hours": 168,
    "store_max_bytes": 128 * 1024 * 1024,
    "store_max_entry_bytes": 32 * 1024 * 1024,
    "store_max_entries": 2_000,
    "recovery_max_bytes": 8_192,
    "recovery_max_matches": 5,
}


def test_defaults_are_the_documented_ones(sutra, hermes_home):
    cfg = sutra.configuration()
    resolved = cfg.as_dict()
    for name, expected in _DEFAULTS.items():
        assert resolved[name] == expected, name
    assert cfg.store_dir == str(hermes_home / "cache" / "sutra-store")
    assert cfg.stats_path == str(hermes_home / "cache" / "sutra-stats.jsonl")


def test_every_consumer_derives_from_the_one_resolved_config(sutra, monkeypatch,
                                                             tmp_path):
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    monkeypatch.setenv("SUTRA_MAX_BYTES", "40000")
    monkeypatch.setenv("SUTRA_HEAD_BYTES", "7000")
    monkeypatch.setenv("SUTRA_TAIL_BYTES", "3000")
    monkeypatch.setenv("SUTRA_STATS_FILE", str(tmp_path / "stats.jsonl"))
    monkeypatch.setenv("SUTRA_STATS_MAX_BYTES", "2048")
    monkeypatch.setenv("SUTRA_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("SUTRA_STORE", "0")
    monkeypatch.setenv("SUTRA_STORE_TTL_HOURS", "48")
    monkeypatch.setenv("SUTRA_STORE_MAX_BYTES", "4096")
    monkeypatch.setenv("SUTRA_STORE_MAX_ENTRY_BYTES", "1024")
    monkeypatch.setenv("SUTRA_STORE_MAX_ENTRIES", "7")
    monkeypatch.setenv("SUTRA_RECOVERY_MAX_BYTES", "4096")
    monkeypatch.setenv("SUTRA_RECOVERY_MAX_MATCHES", "3")

    cfg = sutra.configuration()

    limits = sutra.engine.config_from_env()
    assert (limits.max_bytes, limits.head_bytes, limits.tail_bytes) == (
        cfg.max_bytes, cfg.head_bytes, cfg.tail_bytes)

    store_cfg = sutra.store.StoreConfig.from_env()
    assert store_cfg.directory == cfg.store_dir
    assert store_cfg.enabled is cfg.store_enabled is False
    assert store_cfg.ttl_hours == cfg.store_ttl_hours
    assert store_cfg.max_bytes == cfg.store_max_bytes
    assert store_cfg.max_entry_bytes == cfg.store_max_entry_bytes
    assert store_cfg.max_entries == cfg.store_max_entries

    assert sutra.enabled() is cfg.enabled
    assert sutra.stats_path() == cfg.stats_path
    assert sutra.stats_path() == os.path.abspath(str(tmp_path / "stats.jsonl"))
    assert cfg.stats_max_bytes == 2048


def test_one_operation_parses_the_environment_once(sutra, monkeypatch):
    """One parse per operation, then the resolved object travels with it."""
    calls = []
    real_resolve = sutra.config.resolve
    monkeypatch.setattr(sutra.config, "resolve",
                        lambda: (calls.append(1), real_resolve())[1])
    payload = {"output": "".join(f"line {i:05d}\n" for i in range(4000))}
    out = sutra.compress_result(payload)
    assert out is not payload  # a real rewrite, including retention and the log
    assert len(calls) == 1


def test_engine_honours_the_resolved_budget(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_MAX_BYTES", "500")
    cfg = sutra.configuration()
    assert cfg.max_bytes == 500
    text = "x" * 600
    out, stats = sutra.compress(text)  # no explicit budget: the config decides
    assert out == text
    assert stats["rule"] == "budget-too-small"


def test_store_honours_the_resolved_limits(sutra, monkeypatch, tmp_path):
    monkeypatch.setenv("SUTRA_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("SUTRA_STORE_MAX_ENTRIES", "2")
    cfg = sutra.configuration()
    store = sutra.store.store_from_env()
    assert store.config.max_entries == cfg.store_max_entries == 2
    assert store.dir == cfg.store_dir == str(tmp_path / "store")
    for text in ("a", "b", "c"):
        assert store.put(text) is not None
    assert store.usage()[0] == 2  # the resolved bound is enforced, not just read


def test_recovery_honours_the_resolved_cap(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_RECOVERY_MAX_BYTES", "1024")
    cfg = sutra.configuration()
    assert cfg.recovery_max_bytes == 1024
    entry = sutra.store.store_from_env().put("y" * 3000)
    assert entry is not None
    response = json.loads(sutra.recovery.restore({"id": entry.id, "mode": "full"}))
    assert response["status"] == "too_large"
    assert response["max_bytes"] == cfg.recovery_max_bytes


def test_fail_open_is_honoured_from_the_resolved_config(sutra, monkeypatch):
    payload = {"output": "z" * 40_000}
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    assert sutra.configuration().enabled is False
    assert sutra.compress_result(payload) is payload


@pytest.mark.parametrize("name,value", [
    ("SUTRA_MAX_BYTES", "not-a-number"),
    ("SUTRA_STORE_MAX_ENTRIES", "0"),
    ("SUTRA_STORE_TTL_HOURS", "0"),
    ("SUTRA_RECOVERY_MAX_BYTES", "999999999"),
    ("SUTRA_RECOVERY_MAX_MATCHES", "0"),
])
def test_invalid_values_fall_back_in_one_place(sutra, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    resolved = sutra.configuration().as_dict()
    field = {
        "SUTRA_MAX_BYTES": "max_bytes",
        "SUTRA_STORE_MAX_ENTRIES": "store_max_entries",
        "SUTRA_STORE_TTL_HOURS": "store_ttl_hours",
        "SUTRA_RECOVERY_MAX_BYTES": "recovery_max_bytes",
        "SUTRA_RECOVERY_MAX_MATCHES": "recovery_max_matches",
    }[name]
    assert resolved[field] == _DEFAULTS[field]
