"""The bounded original-output store (store.py).

Put/get round trips, TTL expiry, size and entry quotas, permission and
symlink protection, the disabled-store guardrail, and prune/report
behaviour. No test touches the network or the wider file system.
"""

import hashlib
import os
import stat
import time

import pytest

from conftest import plugin_module

store_mod = plugin_module("store")
Store = store_mod.Store
StoreConfig = store_mod.StoreConfig
StoreUnavailable = store_mod.StoreUnavailable
ID_RE = store_mod.ID_RE


def _config(directory, *, enabled=True, ttl_hours=168,
            max_bytes=StoreConfig.max_bytes,
            max_entry_bytes=StoreConfig.max_entry_bytes,
            max_entries=StoreConfig.max_entries):
    return StoreConfig(
        directory=str(directory), enabled=enabled, ttl_hours=ttl_hours,
        max_bytes=max_bytes, max_entry_bytes=max_entry_bytes,
        max_entries=max_entries,
    )


def test_put_and_get_return_the_exact_bytes(tmp_path):
    store = Store(_config(tmp_path / "store"))
    assert store.available() is True
    data = ("original line %d of output with padding\n" * 2000).encode()

    entry = store.put(data.decode("utf-8"), now=1000.0)
    assert entry is not None
    assert ID_RE.fullmatch(entry.id) is not None
    assert entry.original_bytes == len(data)
    assert entry.created_at == 1000.0

    lookup = store.get(entry.id, now=1000.0)
    assert lookup.status == "ok"
    assert lookup.entry.id == entry.id
    assert store.read_bytes(lookup.entry) == data
    assert store.verify(lookup.entry, data) is True
    assert lookup.entry.sha256 == hashlib.sha256(data).hexdigest()


def test_get_unknown_invalid_and_missing_identifiers(tmp_path):
    store = Store(_config(tmp_path / "store"))
    assert store.get("su_" + "0" * 32).status == "missing"
    assert store.get("not-an-id-at-all").status == "invalid"
    assert store.get("").status == "invalid"
    assert store.get("su_" + "z" * 32).status == "invalid"  # not hex
    assert store.get("su_" + "a" * 31).status == "invalid"  # too short


def test_disabled_store_never_writes_or_reads(tmp_path):
    directory = tmp_path / "store"
    store = Store(_config(directory, enabled=False))
    assert store.put("whatever") is None
    # No payload files may exist under the store directory.
    assert not any(directory.iterdir()) if directory.exists() else True


def test_too_large_entry_is_refused_and_leaves_nothing(tmp_path):
    store = Store(_config(tmp_path / "store", max_entry_bytes=100))
    assert store.put("x" * 200) is None
    assert store.usage() == (0, 0)


def test_ttl_expiry_removes_the_entry(tmp_path):
    store = Store(_config(tmp_path / "store", ttl_hours=1))
    entry = store.put("expire me", now=1000.0)
    assert store.get(entry.id, now=1000.0).status == "ok"
    assert store.get(entry.id, now=1000.0 + 3600 + 1).status == "expired"
    assert not (tmp_path / "store" / (entry.id + ".txt")).exists()


def test_size_quota_prunes_the_oldest_entries(tmp_path):
    store = Store(_config(tmp_path / "store", max_bytes=400,
                          max_entry_bytes=10_000,
                          max_entries=100))
    ids = []
    for now in (1000.0, 1001.0, 1002.0):
        entry = store.put("x" * 150 + "\n", now=now)
        ids.append(entry.id)

    assert store.usage() == (2, 302)
    assert store.get(ids[0], now=1002.0).status == "missing"
    assert store.get(ids[1], now=1002.0).status == "ok"
    assert store.get(ids[2], now=1002.0).status == "ok"


def test_entry_count_cap_prunes_the_oldest_entries(tmp_path):
    store = Store(_config(tmp_path / "store", max_bytes=10_000_000,
                          max_entries=2))
    ids = [store.put(f"entry {i}\n", now=float(i)).id
           for i in range(3)]
    assert store.usage()[0] == 2
    assert store.get(ids[0], now=2.0).status == "missing"
    assert store.get(ids[1], now=2.0).status == "ok"
    assert store.get(ids[2], now=2.0).status == "ok"


def test_prune_sweeps_stale_temp_files_and_orphan_meta(tmp_path):
    directory = tmp_path / "store"
    store = Store(_config(directory))
    real = store.put("keep me")
    old = time.time() - 7200

    junk = directory / ".tmp-abc123.txt"
    junk.write_bytes(b"partial upload")
    os.utime(junk, (old, old))
    orphan = directory / ("su_" + "a" * 32 + ".json")
    orphan.write_text("{}", encoding="utf-8")

    report = store.prune()
    assert report["temp"] == 1
    assert report["orphans"] >= 1
    assert not junk.exists()
    assert not orphan.exists()
    # the real entry survives pruning
    assert store.get(real.id).status == "ok"


def test_store_refuses_a_symlink_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    store = Store(_config(link))
    assert store.available() is False
    with pytest.raises(StoreUnavailable):
        store._ensure_dir()


def test_new_store_directory_is_mode_0700_and_files_are_0600(tmp_path):
    if os.name == "nt":
        pytest.skip("Unix permission bits are not meaningful on Windows")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("mode bits are not enforced for root")
    store = Store(_config(tmp_path / "store"))
    entry = store.put("sensitive original output")
    directory = tmp_path / "store"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    data_file = directory / (entry.id + ".txt")
    assert stat.S_IMODE(data_file.stat().st_mode) == 0o600
    meta_file = directory / (entry.id + ".json")
    assert stat.S_IMODE(meta_file.stat().st_mode) == 0o600


def test_usage_counts_data_files_only(tmp_path):
    store = Store(_config(tmp_path / "store"))
    entry = store.put("a" * 100)
    assert store.usage() == (1, 100)
    store.put("b" * 200)
    assert store.usage() == (2, 300)
    assert store.get(entry.id).status == "ok"


def test_store_path_traversal_id_is_rejected(tmp_path):
    store = Store(_config(tmp_path / "store"))
    assert store.get("../etc/passwd").status == "invalid"
    assert store.get("su_" + "a" * 32 + "/..").status == "invalid"
