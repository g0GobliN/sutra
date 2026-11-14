"""Restore and cleanup accounting, exercised through the real entry points."""

import json
import os
import subprocess
import sys
import time

import pytest

from test_recovery import make_store
from test_sutra import big

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def store(sutra, tmp_path):
    return make_store(sutra, directory=tmp_path / "store")


def _delta(before, after, section, key):
    return after[section].get(key, 0) - before[section].get(key, 0)


def _rule_delta(before, after, rule):
    return (after["compression"]["rules"].get(rule, 0)
            - before["compression"]["rules"].get(rule, 0))


def _stats_rows(sutra):
    with open(sutra.stats_path(), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_restore_outcomes_are_counted(sutra, store):
    before = sutra.metrics_snapshot(store=store)
    entry = store.put("payload line\n" * 100)

    ok = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "bytes", "start": 0, "end": 7}, store=store))
    bad_id = json.loads(sutra.recovery.restore({"id": "../etc/passwd"}, store=store))
    missing = json.loads(sutra.recovery.restore(
        {"id": "su_" + "0" * 32}, store=store))
    bad_range = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "bytes", "start": 9, "end": 2}, store=store))

    expiring = make_store(sutra, directory=store.dir, ttl_hours=1)
    old = expiring.put("old payload", now=1_000)
    expired = json.loads(sutra.recovery.restore({"id": old.id}, store=expiring))

    disabled = make_store(sutra, directory=store.dir, enabled=False)
    unavailable = json.loads(sutra.recovery.restore({"id": entry.id}, store=disabled))

    assert [ok["status"], bad_id["status"], missing["status"], bad_range["status"],
            expired["status"], unavailable["status"]] == [
        "ok", "error", "not_found", "error", "expired", "unavailable"]

    after = sutra.metrics_snapshot(store=store)
    assert _delta(before, after, "restores", "ok") == 1
    assert _delta(before, after, "restores", "bad_id") == 1
    assert _delta(before, after, "restores", "not_found") == 1
    assert _delta(before, after, "restores", "range") == 1
    assert _delta(before, after, "restores", "expired") == 1
    assert _delta(before, after, "restores", "unavailable") == 1
    assert _delta(before, after, "restores", "total") == 6


def test_cleanup_counts_quota_and_ttl(sutra, tmp_path):
    store = make_store(sutra, directory=tmp_path / "clean", max_bytes=1_000,
                       ttl_hours=1)
    before = sutra.metrics_snapshot(store=store)
    base = time.time()
    first = store.put("a" * 600, now=base)
    second = store.put("b" * 600, now=base + 1)  # quota evicts the oldest
    assert first is not None and second is not None
    assert store.get(first.id).status == "missing"

    after_quota = sutra.metrics_snapshot(store=store)
    assert _delta(before, after_quota, "cleanup", "quota") == 1
    assert _delta(before, after_quota, "cleanup", "bytes") >= 600
    assert _delta(before, after_quota, "cleanup", "entries") == 1
    assert _delta(before, after_quota, "cleanup", "runs") == 1

    # TTL runs through the same report, and the bytes it reclaimed are counted.
    report = store.prune(now=base + 2 * 3_600)
    assert report["expired"] == 1
    after_ttl = sutra.metrics_snapshot(store=store)
    assert _delta(after_quota, after_ttl, "cleanup", "expired") == 1
    assert _delta(after_quota, after_ttl, "cleanup", "bytes") == report["bytes"]
    assert after_ttl["store"]["entries"] == store.usage()[0] == 0


def test_snapshot_and_stats_log_agree_on_the_rewrite_subset(sutra, store, monkeypatch):
    """The durable log is the savings subset; the snapshot covers all decisions."""
    before = sutra.metrics_snapshot(store=store)
    sutra.compress_result("tiny output", tool_name="terminal")
    sutra.compress_result(
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        tool_name="terminal")
    sutra.compress_result(big(), tool_name="terminal")
    sutra.compress_result(json.dumps({"log": big(4000)}), tool_name="terminal")

    def boom(*_args, **_kwargs):
        raise RuntimeError("compressor down")

    monkeypatch.setattr(sutra.engine, "_compress_impl", boom)
    sutra.compress_result(big(), tool_name="terminal")
    after = sutra.metrics_snapshot(store=store)
    rows = _stats_rows(sutra)

    assert {"scope", "scope_note", "compression", "restores", "cleanup", "store"} <= set(after)
    assert _delta(before, after, "compression", "total") == 5
    assert _delta(before, after, "compression", "changed") == len(rows) == 2
    assert _delta(before, after, "compression", "unchanged") == 3
    assert all(row["changed"] for row in rows)
    for rule in {row["rule"] for row in rows}:
        assert _rule_delta(before, after, rule) == sum(
            1 for row in rows if row["rule"] == rule)
    for key in ("saved_bytes", "omitted_bytes", "partial", "stored"):
        assert _delta(before, after, "compression", key) == sum(
            int(row[key]) for row in rows), key
    for key in ("original_bytes", "compressed_bytes"):
        assert _delta(before, after, "compression", key) >= sum(
            int(row[key]) for row in rows), key
    assert _delta(before, after, "compression", "fail_open") == 1
    assert after["compression"]["duration_ms_total"] - \
        before["compression"]["duration_ms_total"] >= \
        sum(float(row["duration_ms"]) for row in rows)


def test_unchanged_compressions_stay_out_of_the_stats_log(sutra):
    """A small result is a counter decision, not a savings-log row."""
    assert not os.path.exists(sutra.stats_path())
    before = sutra.metrics_snapshot()
    sutra.compress_result("tiny output", tool_name="terminal")
    sutra.compress_result(
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        tool_name="terminal")
    assert not os.path.exists(sutra.stats_path())
    after = sutra.metrics_snapshot()
    assert _delta(before, after, "compression", "total") == 2
    assert _delta(before, after, "compression", "changed") == 0
    assert _delta(before, after, "compression", "unchanged") == 2


def test_snapshot_is_explicitly_process_scoped_across_a_restart(sutra, monkeypatch):
    """A fresh process must not pretend the durable log's rows are its counters."""
    sutra.compress_result("tiny output", tool_name="terminal")
    sutra.compress_result(
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        tool_name="terminal")
    sutra.compress_result(big(), tool_name="terminal")
    sutra.compress_result(json.dumps({"log": big(4000)}), tool_name="terminal")

    def boom(*_args, **_kwargs):
        raise RuntimeError("compressor down")

    monkeypatch.setattr(sutra.engine, "_compress_impl", boom)
    sutra.compress_result(big(), tool_name="terminal")
    rows_before_restart = _stats_rows(sutra)
    parent = sutra.metrics_snapshot()
    assert parent["compression"]["total"] >= 5
    assert parent["compression"]["changed"] >= 2

    script = """
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    'sutra_plugin', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules['sutra_plugin'] = module
spec.loader.exec_module(module)
print(json.dumps(module.metrics_snapshot()))
"""
    env = dict(os.environ)
    env.pop("SUTRA_STATS_FILE", None)
    result = subprocess.run(
        [sys.executable, "-c", script, ROOT], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout.strip().splitlines()[-1])
    assert snapshot["scope"] == "process"
    assert "restart" in snapshot["scope_note"]
    assert snapshot["compression"]["total"] == 0
    assert snapshot["compression"]["changed"] == 0
    assert "store" in snapshot and "restores" in snapshot and "cleanup" in snapshot
    # The restart added no rows: the durable log is unchanged by the child.
    assert len(_stats_rows(sutra)) == len(rows_before_restart) > 0


def test_compression_metric_failure_never_breaks_the_result(sutra, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("metrics down")

    monkeypatch.setattr(sutra.metrics, "record_compression", boom)
    out = sutra.compress_result(big())
    assert "sutra:" in out


def test_metric_failures_never_break_the_tool(sutra, store, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("metrics down")

    monkeypatch.setattr(sutra.metrics, "record_restore", boom)
    monkeypatch.setattr(sutra.metrics, "record_cleanup", boom)

    entry = store.put("payload\n" * 100)  # put() prunes, so cleanup recording runs
    assert entry is not None
    response = json.loads(sutra.recovery.restore({"id": entry.id}, store=store))
    assert response["status"] == "ok"
    assert sutra.compress_result("line\n" * 5_000)


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_a_symlinked_stats_file_is_not_appended_through(sutra, tmp_path):
    """Regression: the stats log was opened without O_NOFOLLOW.

    A symlink planted at the stats path made SUTRA append its JSON records to
    an unrelated file of the attacker's choosing.
    """
    victim = tmp_path / "victim.txt"
    victim.write_text("original\n")
    stats = sutra.stats_path()
    os.makedirs(os.path.dirname(stats), exist_ok=True)
    os.symlink(victim, stats)

    sutra.compress_result({"output": big()}, tool_name="terminal")

    assert victim.read_text() == "original\n"
    assert os.path.islink(stats)
