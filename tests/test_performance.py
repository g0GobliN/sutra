"""Performance regression guards.

Bounds are intentionally loose: machines vary widely, and these tests
exist to catch pathological slowdowns (quadratic scans, unbounded
retries, missing short-circuits), not to certify precise timings. Each
test also asserts functional outcomes, so a slow pass is still a
correct pass.
"""

import json
import time

from conftest import load_plugin, plugin_module

sutra = load_plugin()
config_mod = plugin_module("config")
Store = plugin_module("store").Store
StoreConfig = plugin_module("store").StoreConfig
recovery_mod = plugin_module("recovery")


def _elapsed(start: float) -> float:
    return time.perf_counter() - start


def test_compress_repeated_calls_stay_bounded(tmp_path, monkeypatch):
    text = "stable output line with some padding\n" * 100
    start = time.perf_counter()
    for _ in range(200):
        output, stats = sutra.compress(text)
        assert isinstance(output, str)
        assert stats.original_bytes == len(text.encode("utf-8"))
    assert _elapsed(start) < 10.0, _elapsed(start)


def test_store_many_puts_stay_under_quota(tmp_path):
    store = Store(StoreConfig(
        directory=str(tmp_path / "store"),
        max_bytes=64_000,
        max_entry_bytes=1_000,
        max_entries=10_000,
    ))
    payloads = [f"payload {i} " + "x" * 190 for i in range(2000)]
    start = time.perf_counter()
    ids = []
    for text in payloads:
        entry = store.put(text)
        assert entry is not None
        ids.append(entry.id)
    assert _elapsed(start) < 30.0, _elapsed(start)

    count, total = store.usage()
    assert total <= 64_000, (count, total)
    # every retained entry still reads back exactly
    for entry_id in ids[-50:]:
        assert store.get(entry_id).status == "ok"


def test_compress_large_compiler_diagnostic_stays_bounded(tmp_path):
    lines = [
        f"src/widget.c:{line}:{col}: warning: unused variable 'v{line % 40}'"
        f" (initialized and not used) [-Wunused-but-set-variable]"
        for line in range(1, 12000)
        for col in (1, 12)
    ]
    diagnostic = "\n".join(lines) + "\n"
    assert len(diagnostic) > 1_000_000

    start = time.perf_counter()
    output, stats = sutra.compress(diagnostic)
    assert _elapsed(start) < 15.0, _elapsed(start)
    assert isinstance(output, str)
    assert stats.original_bytes == len(diagnostic.encode("utf-8"))
    # fail-open guarantee: something round-trips
    assert len(output) > 0


def test_restore_after_many_puts_stays_bounded(tmp_path):
    store = Store(StoreConfig(
        directory=str(tmp_path / "store"),
        max_bytes=1_000_000,
        max_entries=10_000,
    ))
    payload = "restore-me payload segment " * 10
    entry = store.put(payload)
    assert entry is not None

    start = time.perf_counter()
    for _ in range(100):
        payload_json = recovery_mod.restore({"id": entry.id}, store=store)
        assert isinstance(payload_json, str)
    assert _elapsed(start) < 15.0, _elapsed(start)

    restored = json.loads(
        recovery_mod.restore({"id": entry.id, "mode": "full"}, store=store))
    assert restored["status"] == "ok"
    assert "restore-me payload segment" in restored["content"]


def test_config_resolve_repeated_calls_stay_bounded():
    start = time.perf_counter()
    for _ in range(500):
        cfg = config_mod.resolve()
        assert cfg is not None
    assert _elapsed(start) < 10.0, _elapsed(start)
