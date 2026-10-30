"""Tests for the original-output store and the retrieval protocol."""

import ast
import json
import os
import re
import stat
import threading
import time

import pytest

from test_sutra import big

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def make_store(sutra, **overrides):
    cfg = sutra.store.StoreConfig(directory=str(overrides.pop("directory")), **overrides)
    return sutra.store.Store(cfg)


@pytest.fixture()
def store(sutra, tmp_path):
    return make_store(sutra, directory=tmp_path / "store")


# --- the store -------------------------------------------------------------

def test_put_and_read_is_byte_exact(sutra, store):
    text = "line\r\n" * 1000 + "日本語"
    entry = store.put(text, tool_name="terminal", session_id="s1")
    assert entry is not None
    assert store.read_bytes(entry) == text.encode("utf-8")
    assert store.verify(entry) is True
    assert store.get(entry.id).status == "ok"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_store_permissions_are_private(sutra, store):
    entry = store.put("secret-ish")
    assert stat.S_IMODE(os.stat(store.dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(entry.data_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(entry.meta_path).st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_a_preexisting_loose_store_directory_is_tightened(sutra, tmp_path):
    """Regression: 0700 was only applied when the directory was created.

    A store made by an older version, or pre-created in a shared parent, kept
    its traversable mode and leaked every entry name to any local user.
    """
    directory = tmp_path / "store"
    directory.mkdir()
    os.chmod(directory, 0o755)
    store = make_store(sutra, directory=directory)
    entry = store.put("payload")
    assert entry is not None
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    assert store.get(entry.id).status == "ok"


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_symlinked_metadata_is_not_followed(sutra, store, tmp_path):
    """Regression: data reads used O_NOFOLLOW, but metadata used a plain open."""
    entry = store.put("payload")
    elsewhere = tmp_path / "meta-copy.json"
    elsewhere.write_bytes(open(entry.meta_path, "rb").read())
    os.unlink(entry.meta_path)
    os.symlink(elsewhere, entry.meta_path)
    assert store.get(entry.id).status == "missing"


def test_failed_metadata_write_leaves_no_orphan(sutra, store, monkeypatch):
    """Regression: a failed metadata write left the already-written data file.

    The entry is unreadable without its metadata, so the bytes stayed on disk
    (and counted against the quota) until the next full sweep.
    """
    real_write = store._write_atomic

    def flaky(path, data):
        if path.endswith(".json"):
            raise OSError("disk full")
        return real_write(path, data)

    monkeypatch.setattr(store, "_write_atomic", flaky)
    assert store.put("payload") is None
    leftovers = [name for name in os.listdir(store.dir) if name.startswith("su_")]
    assert leftovers == []
    assert store.usage() == (0, 0)


def test_meta_does_not_contain_the_payload(sutra, store):
    entry = store.put("TOPSECRET-" + "x" * 100)
    assert "TOPSECRET" not in open(entry.meta_path, encoding="utf-8").read()


def test_ttl_expiry_deletes_and_reports(sutra, store):
    entry = store.put("payload", now=1_000)
    store.config = sutra.store.StoreConfig(directory=store.dir, ttl_hours=1)
    assert store.get(entry.id, now=1_000 + 3_601).status == "expired"
    assert not os.path.exists(entry.data_path)


def test_quota_prunes_oldest_first(sutra, store):
    store.config = sutra.store.StoreConfig(directory=store.dir, max_bytes=1_000,
                                        max_entries=100)
    base = time.time()
    first = store.put("a" * 600, now=base)
    second = store.put("b" * 600, now=base + 1)
    assert first is not None and second is not None
    assert store.get(first.id).status == "missing"
    assert store.get(second.id).status == "ok"


def test_a_write_under_budget_does_not_scan_the_store(sutra, store, monkeypatch):
    """Regression: every write ran a full expiry sweep, so write cost grew with
    the store's size. Only a due sweep or a breached budget may run one."""
    sweeps = []
    real_prune = store.prune
    monkeypatch.setattr(
        store, "prune", lambda *a, **k: (sweeps.append(1), real_prune(*a, **k))[1])
    store.put("warm-up")  # seeds the in-process totals, sweeps once
    sweeps.clear()
    for _ in range(20):
        assert store.put("payload " * 10) is not None
    assert sweeps == []


def test_entry_count_is_bounded(sutra, store):
    store.config = sutra.store.StoreConfig(directory=store.dir, max_bytes=10_000,
                                        max_entries=2)
    base = time.time()
    entries = [store.put(f"payload-{i}", now=base + i) for i in range(3)]
    assert entries[-1] is not None
    assert store.get(entries[0].id).status == "missing"
    assert store.usage()[0] <= 2


def test_entry_size_cap_refuses(sutra, store):
    store.config = sutra.store.StoreConfig(directory=store.dir, max_entry_bytes=100)
    assert store.put("x" * 200) is None


def test_store_too_small_for_the_entry_returns_none(sutra, store):
    store.config = sutra.store.StoreConfig(directory=store.dir, max_bytes=100,
                                        max_entry_bytes=1_000)
    assert store.put("x" * 500) is None
    assert store.usage() == (0, 0)


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_symlinked_store_directory_is_refused(sutra, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    sap = sutra.store.Store(sutra.store.StoreConfig(directory=str(link)))
    assert sap.put("payload") is None
    assert sap.available() is False


def test_data_without_meta_is_not_served(sutra, store):
    entry = store.put("payload")
    os.unlink(entry.meta_path)
    assert store.get(entry.id).status == "missing"


def test_meta_without_data_is_not_served(sutra, store):
    entry = store.put("payload")
    os.unlink(entry.data_path)
    assert store.get(entry.id).status == "missing"
    assert not os.path.exists(entry.meta_path), "the orphaned meta should be cleaned"


def test_truncated_write_is_not_served(sutra, store):
    entry = store.put("payload payload payload")
    with open(entry.data_path, "wb") as fh:
        fh.write(b"pay")
    assert store.get(entry.id).status == "missing"


def test_prune_removes_stale_temp_files(sutra, store):
    store.put("payload")
    tmp = os.path.join(store.dir, ".tmp-leftover")
    with open(tmp, "wb") as fh:
        fh.write(b"partial")
    os.utime(tmp, (0, 0))
    store.prune()
    assert not os.path.exists(tmp)


def test_concurrent_puts_and_reads(sutra, store):
    errors = []
    ids = []

    base = time.time()

    def writer(index):
        try:
            entry = store.put(f"payload {index} " * 50, now=base + index)
            if entry is None:
                errors.append(f"put {index} returned None")
            else:
                ids.append(entry.id)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    for entry_id in ids:
        lookup = store.get(entry_id)
        assert lookup.status == "ok", entry_id
        assert store.verify(lookup.entry) is True


def test_delete_is_idempotent(sutra, store):
    entry = store.put("payload")
    store.delete(entry.id)
    store.delete(entry.id)
    assert store.get(entry.id).status == "missing"


def test_path_traversal_is_rejected(sutra, store):
    for bad in ("../../etc/passwd", "su_../x", "/etc/passwd", "su_" + "0" * 31):
        assert store.get(bad).status == "invalid"


# --- the protocol ----------------------------------------------------------

def _compressed(sutra):
    """A real compressed payload plus the id and offsets its marker advertises."""
    log = "\n".join(f"log line {i}" for i in range(2000)) + \
        "\nValueError: needle-in-the-middle\n" + \
        "\n".join(f"tail line {i}" for i in range(1000))
    stored = sutra.compress_result({"output": log}, tool_name="terminal")["output"]
    entry_id = ID_RE.search(stored).group(1)
    start, end = (int(x) for x in re.search(r"start=(\d+), end=(\d+)", stored).groups())
    return log, stored, entry_id, start, end


def test_bytes_mode_pages_out_the_exact_original_slice(sutra):
    log, stored, entry_id, start, end = _compressed(sutra)
    expected = log.encode("utf-8")[start:end].decode("utf-8")
    pages, cursor = [], start
    for _ in range(100):
        response = json.loads(sutra.recovery.restore(
            {"id": entry_id, "mode": "bytes", "start": cursor, "end": end}))
        assert response["status"] == "ok"
        pages.append(response["content"])
        if not response.get("next"):
            break
        cursor = response["next"]["start"]
    else:
        pytest.fail("pagination did not terminate")
    assert len(pages) > 1, "the elided range is larger than one page"
    assert "".join(pages) == expected
    assert "needle-in-the-middle" in "".join(pages)


def test_byte_offsets_address_the_stored_original_after_dedupe_and_ansi(sutra):
    """Regression: offsets must index the stored bytes, not a normalised copy."""
    head = "".join("\x1b[32mrepeated line\x1b[0m\n" for _ in range(2_000))
    middle = "".join(f"unique line {i} needle-{i}\n" for i in range(2_000))
    tail = "".join(f"tail repeated {i}\n" for i in range(300))
    log = head + middle + tail
    stored = sutra.compress_result({"output": log})["output"]
    assert "identical line(s) omitted" in stored, "the head should have been de-duplicated"
    marker = re.search(r"\[sutra: partial[^\]]*\]", stored).group(0)
    entry_id = ID_RE.search(marker).group(1)
    start, end = (int(x) for x in re.search(r"start=(\d+), end=(\d+)", marker).groups())
    expected = log.encode("utf-8")[start:end].decode("utf-8")
    pages, cursor = [], start
    for _ in range(100):
        response = json.loads(sutra.recovery.restore(
            {"id": entry_id, "mode": "bytes", "start": cursor, "end": end}))
        assert response["status"] == "ok"
        pages.append(response["content"])
        if not response.get("next"):
            break
        cursor = response["next"]["start"]
    assert "".join(pages) == expected
    assert "needle-1000" in "".join(pages)


def test_search_mode_finds_the_middle(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "search", "query": "needle-in-the-middle", "context": 1}))
    assert response["status"] == "ok"
    assert response["matches_found"] == 1
    assert "needle-in-the-middle" in response["content"]
    assert "untrusted data" in response["content_note"]


def test_byte_mode_rejects_ranges_that_split_a_character(sutra, store):
    """A range that cuts through UTF-8 used to return ok with fewer bytes than
    its own range claimed, and with no way to ask for the missing part."""
    entry = store.put("a€b")
    for start, end in ((0, 2), (1, 3), (2, 4), (0, 3)):
        response = json.loads(sutra.recovery.restore(
            {"id": entry.id, "mode": "bytes", "start": start, "end": end}, store=store))
        assert response["status"] == "error", (start, end, response)
        assert "splits a UTF-8 character" in response["error"]
        assert "content" not in response

    for start, end, expected in ((0, 1, "a"), (1, 4, "€"), (4, 5, "b"), (0, 5, "a€b")):
        response = json.loads(sutra.recovery.restore(
            {"id": entry.id, "mode": "bytes", "start": start, "end": end}, store=store))
        assert response["status"] == "ok"
        assert response["content"] == expected
        assert response["complete"] is True


def test_byte_mode_preserves_literal_replacement_characters(sutra, store):
    """U+FFFD is valid content, not proof of a partial decode. The old cleanup
    stripped it at a range edge and still reported the range complete."""
    entry = store.put("\ufffdabc\ufffdz")
    for start, end, expected in ((0, 3, "\ufffd"), (3, 6, "abc"),
                                 (6, 9, "\ufffd"), (9, 10, "z"),
                                 (0, 10, "\ufffdabc\ufffdz")):
        response = json.loads(sutra.recovery.restore(
            {"id": entry.id, "mode": "bytes", "start": start, "end": end}, store=store))
        assert response["status"] == "ok", (start, end, response)
        assert response["content"] == expected, (start, end, response)
        assert response["returned_bytes"] == len(expected.encode("utf-8"))
        assert response["complete"] is True


def test_byte_mode_preserves_a_replacement_character_at_a_page_start(sutra, store):
    """A long page that starts on a literal U+FFFD must return it; the old
    lstrip removed it while next.start advanced past its bytes."""
    text = "a" * 30_000 + "\ufffd" + "b" * 30_000
    entry = store.put(text)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "bytes", "start": 30_000,
         "end": len(text.encode("utf-8")), "max_bytes": 512}, store=store))
    assert response["status"] == "ok"
    assert response["content"].startswith("\ufffd"), response["content"][:20]
    assert response["returned_bytes"] == 512
    assert response["next"]["start"] == 30_512


def test_case_insensitive_search_keeps_the_match_in_a_clipped_line(sutra, store):
    """Lowercasing can expand a character (İ → i̇); mapping that index back to
    the original line used to clip the window past the match it announced."""
    line = "İ" * 3000 + "x" * 2000 + "NEEDLE-AT-END" + "y" * 8000
    entry = store.put(line)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "search", "query": "NEEDLE-AT-END",
         "case_sensitive": False, "max_bytes": 8192}, store=store))
    assert response["status"] == "ok"
    assert response["matches_found"] == 1
    assert "NEEDLE-AT-END" in response["content"]
    assert response["complete"] is True
    assert len(response["content"].encode("utf-8")) <= 8192


def test_search_treats_regex_metacharacters_literally(sutra, store):
    entry = store.put("a.*b\naxxb\n")
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "search", "query": ".*",
         "case_sensitive": False}, store=store))
    assert response["status"] == "ok"
    assert response["matches_found"] == 1
    assert "a.*b" in response["content"]


def test_retrieval_at_the_entry_cap_stays_within_its_bounds(sutra, tmp_path):
    """The execution envelope starts with hard work bounds: one 32 MiB entry,
    at most 64 KiB returned, at most the requested matches, every mode an
    answer rather than a hang or an unbounded payload. Wall-clock evidence is
    in bench/retrieval.py; this holds the structural half."""
    cap = 32 * 1024 * 1024
    lines = [f"{i:08d} " + "x" * 70 for i in range(400_000)]
    lines[-1] = "needle at the end " + "x" * 62
    text = "\n".join(lines) + "\n"
    store = make_store(sutra, directory=tmp_path / "big-store",
                       max_entry_bytes=cap, max_bytes=2 * cap)
    entry = store.put(text)
    assert entry is not None
    cases = [
        ({"id": entry.id, "mode": "full"}, {"too_large"}),
        ({"id": entry.id, "mode": "bytes", "start": 0, "end": 65_536}, {"ok"}),
        ({"id": entry.id, "mode": "lines", "start": 1, "end": 101}, {"ok"}),
        ({"id": entry.id, "mode": "search", "query": "no-such-token"}, {"ok"}),
        ({"id": entry.id, "mode": "search", "query": "needle at the end"}, {"ok"}),
    ]
    for args, statuses in cases:
        response = json.loads(sutra.recovery.restore(args, store=store))
        assert response["status"] in statuses, (args, response)
        assert len(response.get("content", "").encode("utf-8")) <= 65_536
        if args["mode"] == "search":
            assert response["matches_found"] <= 20


def test_lines_mode_returns_a_range_and_says_what_remains(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "lines", "start": 1, "end": 3}))
    assert response["status"] == "ok"
    assert response["range"] == {"start": 1, "end": 3}
    assert response["complete"] is True
    assert response["more"] is True
    assert "log line 0" in response["content"]


def test_full_mode_verifies_the_hash(sutra):
    log, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "full", "max_bytes": 65536}))
    assert response["status"] == "ok"
    assert response["content"] == log
    assert response["sha256_verified"] is True


def test_full_mode_refuses_an_oversized_payload(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore({"id": entry_id, "mode": "full"}))
    assert response["status"] == "too_large"
    assert response["more"] is True


def test_full_mode_uses_the_store_verifier(sutra, store, monkeypatch):
    """Recovery must report the store's integrity check, not its own hash."""
    entry = store.put("payload\n" * 100)
    monkeypatch.setattr(store, "verify", lambda _entry, _data=None: False)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "full", "max_bytes": 65_536}, store=store))
    assert response["status"] == "ok"
    assert response["sha256_verified"] is False


def test_store_verifies_the_bytes_it_is_given(store):
    """The verifier judges supplied content, not only a fresh file read."""
    payload = ("payload\n" * 100).encode("utf-8")
    entry = store.put(payload.decode("utf-8"))
    assert entry is not None
    assert store.verify(entry, payload) is True
    corrupted = payload[:-1] + bytes([payload[-1] ^ 0xFF])
    assert len(corrupted) == len(payload)
    assert store.verify(entry, corrupted) is False


def test_full_restore_reads_the_entry_once(sutra, store, monkeypatch):
    """sha256_verified must describe the bytes returned, from one file read."""
    payload = "\n".join(f"line {i}: " + "x" * 20 for i in range(1_500))
    assert 40_000 < len(payload) < 65_536
    entry = store.put(payload)
    opens = []
    real_open = os.open

    def counting_open(path, *args, **kwargs):
        if os.fspath(path) == entry.data_path:
            opens.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", counting_open)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "full", "max_bytes": 65_536}, store=store))
    assert response["status"] == "ok"
    assert response["content"] == payload
    assert response["sha256_verified"] is True
    assert len(opens) == 1


def test_full_mode_reports_a_corrupted_entry(sutra, store):
    entry = store.put("payload\n" * 100)
    with open(entry.data_path, "r+b") as fh:
        first = fh.read(1)
        fh.seek(0)
        fh.write(b"X" if first != b"X" else b"Y")
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "full", "max_bytes": 65_536}, store=store))
    assert response["status"] == "ok"
    assert response["sha256_verified"] is False
    assert "hash" in response["note"]


def test_lines_mode_is_bounded(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "lines", "start": 1, "end": 4000, "max_bytes": 1024}))
    assert response["status"] == "ok"
    assert len(response["content"].encode("utf-8")) <= 1024
    assert response["complete"] is False
    assert response["next"]["start"] > 1


def test_search_respects_max_matches_and_reports_more(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "search", "query": "log line", "max_matches": 2}))
    assert response["matches_found"] == 2
    assert response["more"] is True


def test_search_is_literal_and_case_sensitive_by_default(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    missing = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "search", "query": "NEEDLE-IN-THE-MIDDLE"}))
    assert missing["matches_found"] == 0
    found = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "search", "query": "NEEDLE-IN-THE-MIDDLE",
         "case_sensitive": False}))
    assert found["matches_found"] == 1


def test_query_length_is_bounded(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "search", "query": "x" * 201}))
    assert response["status"] == "error"


def test_invalid_mode_is_an_error(sutra):
    _, _, entry_id, _, _ = _compressed(sutra)
    response = json.loads(sutra.recovery.restore({"id": entry_id, "mode": "regex"}))
    assert response["status"] == "error"


def test_missing_and_malformed_ids(sutra):
    assert json.loads(sutra.recovery.restore({}))["status"] == "error"
    assert json.loads(sutra.recovery.restore({"id": 5}))["status"] == "error"
    assert json.loads(sutra.recovery.restore(
        {"id": "../../etc/passwd"}))["status"] == "error"
    assert json.loads(sutra.recovery.restore(
        {"id": "su_" + "0" * 32}))["status"] == "not_found"


def test_restore_never_raises(sutra):
    for args in (None, [], {}, {"id": None}, {"id": "su_" + "f" * 32, "mode": 3}):
        raw = sutra.recovery.restore(args)
        assert isinstance(raw, str)
        assert json.loads(raw)["status"] in {
            "ok", "error", "not_found", "expired", "unavailable", "too_large"}


def test_cleanup_between_lookup_and_read_is_reported(sutra, store, monkeypatch):
    entry = store.put("payload")
    lookup = store.get(entry.id)
    assert lookup.status == "ok"
    store.delete(entry.id)
    response = json.loads(sutra.recovery.restore({"id": entry.id, "mode": "full"}))
    assert response["status"] == "not_found"


def test_disabled_store_marks_the_result_as_not_retained(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_STORE", "0")
    stored = sutra.compress_result({"output": big()})["output"]
    assert "original not retained" in stored
    assert not ID_RE.search(stored)
    response = json.loads(sutra.recovery.restore({"id": "su_" + "0" * 32}))
    assert response["status"] == "unavailable"


# --- JSON field scope ------------------------------------------------------

def test_field_scope_returns_the_exact_field_text(sutra):
    lines = [f"line {i}" for i in range(6000)]
    payload = json.dumps({"output": "\n".join(lines), "exit_code": 0})
    stored = json.loads(sutra.compress_result(payload))["output"]
    marker = re.search(r"\[sutra: partial[^\]]*\]", stored).group(0)
    entry_id = ID_RE.search(marker).group(1)
    assert 'field="/output"' in marker
    start, end = (int(x) for x in re.search(r"start=(\d+), end=(\d+)", marker).groups())
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "bytes", "field": "/output", "start": start,
         "end": start + 500}))
    assert response["status"] == "ok"
    assert response["content"] == '\n'.join(lines).encode("utf-8")[start:start + 500].decode("utf-8")
    # payload_bytes describes the scope, document_bytes the stored document.
    assert response["payload_bytes"] == response["original_bytes"]
    assert response["document_bytes"] > response["original_bytes"]
    assert response["range"] == {"start": start, "end": start + 500}


def test_field_pointer_supports_nested_paths(sutra, tmp_path):
    lines = [f"row {i}" for i in range(8000)]
    payload = json.dumps({"items": [{"log": "\n".join(lines)}]})
    stored = json.loads(sutra.compress_result(payload))["items"][0]["log"]
    marker = re.search(r"\[sutra: partial[^\]]*\]", stored).group(0)
    entry_id = ID_RE.search(marker).group(1)
    assert 'field="/items/0/log"' in marker
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "lines", "field": "/items/0/log", "start": 1,
         "end": 2}))
    assert response["status"] == "ok"
    assert "row 0" in response["content"]


def _marker_in(value):
    """The first marker line anywhere in a decoded JSON document."""
    if isinstance(value, str):
        found = re.search(r"\[sutra:[^\]]*\]", value)
        return found.group(0) if found else None
    if isinstance(value, dict):
        for item in value.values():
            marker = _marker_in(item)
            if marker:
                return marker
    if isinstance(value, list):
        for item in value:
            marker = _marker_in(item)
            if marker:
                return marker
    return None


def _field_round_trip(sutra, payload, expected, expected_field):
    """Compress, take the field verbatim from the marker, restore those bytes."""
    stored = sutra.compress_result(payload)
    assert stored != payload
    marker = _marker_in(json.loads(stored))
    assert marker is not None and "field=" in marker
    field = re.search(r'field="([^"]+)"', marker).group(1)
    assert field == expected_field
    assert len(field.encode("utf-8")) <= sutra.config.MAX_FIELD_POINTER
    entry_id = ID_RE.search(marker).group(1)
    start, end = (int(x) for x in re.search(r"start=(\d+), end=(\d+)", marker).groups())
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "field": field, "mode": "bytes", "start": start,
         "end": end, "max_bytes": 65_536}))
    assert response["status"] == "ok"
    assert response["content"] == expected.encode("utf-8")[start:end].decode("utf-8")


def test_deep_field_pointer_resolves_to_that_field(sutra):
    """Regression: a path over 64 characters was truncated to `…` plus a tail,
    which recovery rejected — and dropping the field returned other bytes."""
    leaf = "\n".join(f"row {i}" for i in range(6000))
    node = {"leaf": leaf}
    for i in range(6):
        node = {f"level{i}_long_key": node}
    stored = sutra.compress_result(json.dumps(node))
    marker = _marker_in(json.loads(stored))
    assert marker is not None and 'field="/level5_long_key/level4_long_key/' in marker
    field = re.search(r'field="([^"]+)"', marker).group(1)
    assert field.startswith("/")
    assert len(field.encode("utf-8")) > 64  # the old truncation point
    entry_id = ID_RE.search(marker).group(1)
    start, end = (int(x) for x in re.search(r"start=(\d+), end=(\d+)", marker).groups())
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "field": field, "mode": "bytes", "start": start,
         "end": end, "max_bytes": 65_536}))
    assert response["status"] == "ok"
    assert response["content"] == leaf.encode("utf-8")[start:end].decode("utf-8")


def test_field_pointer_limit_is_shared_by_producer_and_consumer(sutra):
    """The engine must not be able to emit a pointer restore would reject."""
    assert sutra.engine._MAX_FIELD_PATH == sutra.recovery.MAX_FIELD_POINTER
    assert sutra.config.MAX_FIELD_POINTER == sutra.recovery.MAX_FIELD_POINTER


def test_field_pointer_over_the_budget_is_not_truncated(sutra):
    """Over budget is a refusal, not a `…`-shortened pointer recovery rejects."""
    leaf = "\n".join(f"row {i}" for i in range(6000))
    payload = json.dumps({"k" * 300: leaf})
    out = sutra.compress_result(payload)
    assert out == payload
    assert "field=" not in out


def test_field_pointer_escapes_slash_and_tilde(sutra):
    leaf = "\n".join(f"row {i}" for i in range(6000))
    _field_round_trip(sutra, json.dumps({"a/b": {"c~d": leaf}}), leaf, "/a~1b/c~0d")


def test_array_index_field_pointer_round_trips(sutra):
    leaf = "\n".join(f"row {i}" for i in range(6000))
    _field_round_trip(sutra, json.dumps({"items": [{"logs": [leaf]}]}), leaf,
                      "/items/0/logs/0")


def test_field_with_unquotable_key_is_left_alone(sutra):
    """A pointer with a quote, backslash, newline, `]` or `)` cannot be shown
    verbatim in a marker, so that field must not be elided at all."""
    leaf = "\n".join(f"row {i}" for i in range(6000))
    payload = json.dumps({'a"b': {"c)d": leaf}})
    out = sutra.compress_result(payload)
    assert out == payload  # no marker, no lossy pointer


def test_unresolvable_field_pointer_errors_without_content(sutra):
    payload = json.dumps({"output": big(4000)})
    stored = sutra.compress_result(payload)
    entry_id = ID_RE.search(stored).group(1)
    for field in ("/missing", "…/output", "/output/0"):
        response = json.loads(sutra.recovery.restore(
            {"id": entry_id, "field": field, "mode": "full"}))
        assert response["status"] == "error"
        assert "content" not in response


def test_field_errors_are_explicit(sutra):
    payload = json.dumps({"output": big(4000), "n": 3})
    stored = sutra.compress_result(payload)
    entry_id = ID_RE.search(stored).group(1)
    missing = json.loads(sutra.recovery.restore(
        {"id": entry_id, "field": "/nope", "mode": "full"}))
    assert missing["status"] == "error"
    not_string = json.loads(sutra.recovery.restore(
        {"id": entry_id, "field": "/n", "mode": "full"}))
    assert not_string["status"] == "error"
    bad_pointer = json.loads(sutra.recovery.restore(
        {"id": entry_id, "field": "output", "mode": "full"}))
    assert bad_pointer["status"] == "error"


# --- review regressions ----------------------------------------------------
# Found by an independent adversarial review of the store and recovery code.
# Each test fails on the pre-review implementation.

def test_truncated_page_that_ends_at_the_last_line_reports_more(sutra):
    """`more` used to mirror `end < total`, so a budget-truncated final page
    looked complete unless the caller also checked `complete`."""
    entry = sutra.store.store_from_env().put("\n".join(f"l{i:04d} " + "x" * 60
                                                    for i in range(400)))
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "lines", "start": 1, "end": 400, "max_bytes": 512}))
    assert response["complete"] is False
    assert response["more"] is True
    assert response["next"]["start"] > 1


def test_empty_or_reversed_byte_range_is_an_error_not_a_success(sutra):
    entry = sutra.store.store_from_env().put("0123456789")
    for start, end in ((50, 10), (5, 5), (0, 0)):
        response = json.loads(sutra.recovery.restore(
            {"id": entry.id, "mode": "bytes", "start": start, "end": end}))
        assert response["status"] == "error", (start, end, response)
        assert "range" in response


def test_returned_bytes_counts_the_text_actually_returned(sutra):
    """Paging stops on a character boundary, so the raw chunk can be longer
    than the text handed back; returned_bytes must describe the text."""
    entry = sutra.store.store_from_env().put("a" + "€" * 800)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "bytes", "max_bytes": 512}))
    assert response["status"] == "ok"
    assert response["returned_bytes"] == len(response["content"].encode("utf-8"))

    # And paging still reproduces every byte exactly.
    collected = b""
    args = {"id": entry.id, "mode": "bytes", "max_bytes": 512}
    for _ in range(10):
        page = json.loads(sutra.recovery.restore(args))
        collected += page["content"].encode("utf-8")
        if not page.get("next"):
            break
        args = {"id": entry.id, **{k: v for k, v in page["next"].items()},
                "max_bytes": 512}
    assert collected == ("a" + "€" * 800).encode("utf-8")


def test_field_scope_reports_the_scope_size_not_the_document_size(sutra):
    payload = json.dumps({"output": big(3000), "pad": "z" * 5_000})
    stored = json.loads(sutra.compress_result(payload))["output"]
    entry_id = ID_RE.search(stored).group(1)
    response = json.loads(sutra.recovery.restore(
        {"id": entry_id, "field": "/output", "mode": "bytes", "start": 0, "end": 10}))
    assert response["payload_bytes"] == response["original_bytes"]
    assert response["document_bytes"] > response["payload_bytes"]


def test_negative_array_index_is_rejected(sutra):
    """`/items/-1` used to select the last element; JSON Pointer has no
    negative indices."""
    payload = json.dumps({"items": ["a", "b"]})
    entry = sutra.store.store_from_env().put(payload)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "field": "/items/-1", "mode": "full"}))
    assert response["status"] == "error"


def test_search_counts_matched_lines_not_blocks(sutra):
    text = "\n".join(["alpha", "context", "alpha", "context", "alpha"])
    entry = sutra.store.store_from_env().put(text)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "search", "query": "alpha", "context": 1}))
    assert response["status"] == "ok"
    assert response["matched_blocks"] == 1
    assert response["matches_found"] == 3
    assert response["matches_returned"] == 3


def test_prune_removes_unreadable_metadata_with_its_data(sutra, store, tmp_path):
    entry = store.put("payload", now=1_000)
    with open(entry.meta_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    old = 1_000 - 2 * 3_600
    os.utime(entry.data_path, (old, old))
    os.utime(entry.meta_path, (old, old))
    report = store.prune(now=1_000)
    assert report["orphans"] == 1
    assert not os.path.exists(entry.data_path)
    assert not os.path.exists(entry.meta_path)


# --- every marker must suggest a call that actually works -------------------

def _calls(text):
    """Every sutra_restore(...) the marker suggests, verbatim."""
    return re.findall(r"sutra_restore\([^)]*\)", text)


def _bind(hint):
    """Parse the hint as a call and bind its literal keyword arguments."""
    call = ast.parse(hint, mode="eval").body
    assert isinstance(call, ast.Call), hint
    assert getattr(call.func, "id", "") == "sutra_restore", hint
    return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}


def _assert_hint_returns_evidence(sutra, hint, name=""):
    """Run a marker's call verbatim and require bytes or matches back."""
    kwargs = _bind(hint)
    assert kwargs["id"].startswith("su_"), (name, hint)
    assert kwargs["mode"] in {"bytes", "lines", "search", "full"}
    response = json.loads(sutra.recovery.handle_tool_call(kwargs))
    assert response["status"] == "ok", (name, hint, response)
    if kwargs["mode"] == "search":
        assert response["matches_found"] > 0, (name, hint, response)
    assert response["content"], (name, hint, response)
    following = response.get("next")
    if following:
        follow = dict(kwargs)
        follow.update(following)
        page = json.loads(sutra.recovery.handle_tool_call(follow))
        assert page["status"] == "ok", (name, follow, page)
        assert page.get("content") or page.get("matches_found"), (name, follow, page)
    return kwargs, response


def test_every_marker_hint_parses_and_runs(sutra):
    """Regression: the JSON-field hint had no comma before mode=, so the one
    string an agent copies to recover evidence was not a valid call."""
    traceback = ("Traceback (most recent call last):\n"
                 + "".join(f'  File "f{i}.py", line {i}, in f\n    x = 1\n'
                           for i in range(800))
                 + "ValueError: boom\n")
    cases = {
        "text": big(),
        "traceback": traceback,
        "json": json.dumps({"output": big(3_000)}),
        "json-dedupe": json.dumps({"log": "same line\n" * 5_000}),
        "repeats": "same line\n" * 4_000 + big(200),
    }
    for name, text in cases.items():
        out = sutra.compress_result(text)
        assert out != text, name
        # A JSON document carries the marker inside a string field.
        if name.startswith("json"):
            readable = next(v for v in json.loads(out).values() if isinstance(v, str))
        else:
            readable = out
        hints = _calls(readable)
        assert hints, name
        for hint in hints:
            _assert_hint_returns_evidence(sutra, hint, name)

    # A diff is protected end to end: no rewrite, so no marker and no call.
    diff = ("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
            + big())
    protected = sutra.compress_result(diff)
    assert protected == diff
    assert not _calls(protected)


def test_repeat_collapse_hint_returns_the_omitted_lines(sutra):
    """The old hint said `query="<the repeated line>"`: run verbatim, it
    returned zero matches. The marker must quote a range that holds the run."""
    repeated = "the same failure line"
    out = sutra.compress_result((repeated + "\n") * 4_000 + big(200))
    marker = next(line for line in out.splitlines()
                  if "identical line(s) omitted" in line)
    hints = _calls(marker)
    assert len(hints) == 1, marker
    assert "query=" not in hints[0] and "<" not in hints[0], hints[0]
    kwargs, response = _assert_hint_returns_evidence(sutra, hints[0])
    assert kwargs["mode"] == "lines"
    assert kwargs["start"] >= 1
    assert repeated in response["content"]


def test_tail_dedupe_hint_uses_original_line_numbers(sutra):
    """A tail fragment starts part-way into the original; its line numbers
    must be offset by the lines before it, not restart at 1."""
    repeated = "tail repeat line"
    body = "\n".join(f"body {i}" for i in range(2_000))
    text = body + "\n" + (repeated + "\n") * 4_000
    out = sutra.compress_result(text)
    marker = next(line for line in out.splitlines()
                  if "identical line(s) omitted" in line)
    kwargs, response = _assert_hint_returns_evidence(sutra, _calls(marker)[0])
    assert kwargs["mode"] == "lines"
    assert repeated in response["content"]
    assert text.splitlines()[kwargs["start"] - 1] == repeated


def test_json_field_dedupe_hint_returns_the_omitted_lines(sutra):
    """The old hint asked for mode=full on a 30 kB field, which the default
    response cap refuses; a line range returns the omitted lines instead."""
    repeated = "repeated json field line"
    payload = json.dumps({"log": (repeated + "\n") * 1_200})
    assert len(payload.encode("utf-8")) > 28_000
    out = sutra.compress_result(payload)
    inner = json.loads(out)["log"]
    hint = next(h for h in _calls(inner)
                if 'field="/log"' in h and 'mode="lines"' in h)
    kwargs, response = _assert_hint_returns_evidence(sutra, hint)
    assert kwargs["field"] == "/log"
    assert response["range"]["end"] > response["range"]["start"]
    assert repeated in response["content"]
    # Proof that the old suggestion could not satisfy its own cap.
    old = dict(kwargs)
    old["mode"] = "full"
    assert json.loads(sutra.recovery.handle_tool_call(old))["status"] == "too_large"


def test_unaddressable_line_breaks_quote_no_hint(sutra):
    """A form feed makes `splitlines()` and `StringIO` disagree about line
    numbers, so the marker states that and quotes no call instead."""
    out = sutra.compress_result("same\x0cline\n" * 3_000 + "tail\n")
    marker = next(line for line in out.splitlines()
                  if "identical line(s) omitted" in line)
    assert "sutra_restore" not in marker, marker
    assert "cannot be addressed by line number" in marker, marker


def test_lines_hint_that_clips_a_line_offers_no_empty_next(sutra):
    entry = sutra.store.store_from_env().put("L" * 30_000)
    response = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "lines", "start": 1, "end": 2}))
    assert response["status"] == "ok"
    assert response["content"]
    assert response.get("next") is None
    assert "clipped" in response["note"]


MARKER_ID = "su_" + "0123456789abcdef" * 2


def _markers(sutra):
    """Every marker shape the engine can emit, rendered with fixed inputs."""
    engine = sutra.engine
    ref = engine.Ref(MARKER_ID, 168)
    return {
        "plain": engine._marker_line(1_000, "large output", ref, total=10_000,
                                     start=10, end=9_000),
        "plain-no-ttl": engine._marker_line(1_000, "large output",
                                            engine.Ref(MARKER_ID, 0), total=10_000,
                                            start=10, end=9_000),
        "traceback": engine._marker_line(500, "traceback: error end kept",
                                         engine.Ref(MARKER_ID, 48), total=5_000,
                                         start=1, end=4_000),
        "json-field": engine._marker_line(40_470, "large output",
                                          engine.Ref(MARKER_ID, 168, field="/output"),
                                          total=85_921, start=12_280, end=52_750),
        "not-retained": engine._marker_line(45_231, "large output", None, total=53_600),
        "json-dedupe": engine._dedupe_marker(
            5_000, engine.Ref(MARKER_ID, 168, field="/log"), 12),
        "json-dedupe-no-lines": engine._dedupe_marker(
            5_000, engine.Ref(MARKER_ID, 168, field="/log")),
        "repeats": engine._dedupe_marker(644, engine.Ref(MARKER_ID, 168), 3),
        "repeats-no-lines": engine._dedupe_marker(
            644, engine.Ref(MARKER_ID, 168)),
        "repeats-not-retained": engine._dedupe_marker(12, None),
    }


def test_every_marker_renders_exactly(sutra):
    """Presentation is asserted as text, not only as a parseable call: the
    regression was a doubled separator ("· · id="), which still parsed."""
    i = MARKER_ID
    expected = {
        "plain": (f'[sutra: partial · 1,000 bytes elided (large output) · id={i} · '
                  f'original 10,000 B retained 168h · retrieve: sutra_restore('
                  f'id="{i}", mode="bytes", start=10, end=9000)]'),
        "plain-no-ttl": (f'[sutra: partial · 1,000 bytes elided (large output) · '
                         f'id={i} · original 10,000 B retained · retrieve: '
                         f'sutra_restore(id="{i}", mode="bytes", start=10, '
                         f'end=9000)]'),
        "traceback": (f'[sutra: partial · 500 bytes elided (traceback: error end kept) '
                      f'· id={i} · original 5,000 B retained 48h · retrieve: '
                      f'sutra_restore(id="{i}", mode="bytes", start=1, end=4000)]'),
        "json-field": (f'[sutra: partial · 40,470 bytes elided (large output) · '
                       f'field="/output" · id={i} · original 85,921 B retained 168h · '
                       f'retrieve: sutra_restore(id="{i}", field="/output", '
                       f'mode="bytes", start=12280, end=52750)]'),
        "not-retained": ('[sutra: partial · 45,231 bytes elided (large output) · '
                         'original not retained · re-run the tool with a narrower '
                         'query (grep/head/offset) to see the middle]'),
        "json-dedupe": (f'[sutra: 5,000 identical line(s) omitted · field="/log" · '
                        f'id={i} · retrieve: sutra_restore(id="{i}", '
                        f'field="/log", mode="lines", start=12, end=5012)]'),
        "json-dedupe-no-lines": (
            f'[sutra: 5,000 identical line(s) omitted · field="/log" · id={i} · '
            f'the omitted lines cannot be addressed by line number in this '
            f'result]'),
        "repeats": (f'[sutra: 644 identical line(s) omitted · id={i} · retrieve: '
                    f'sutra_restore(id="{i}", mode="lines", start=3, end=647)]'),
        "repeats-no-lines": (
            f'[sutra: 644 identical line(s) omitted · id={i} · the omitted lines '
            f'cannot be addressed by line number in this result]'),
        "repeats-not-retained": '[sutra: 12 more identical line(s) omitted]',
    }
    rendered = _markers(sutra)
    assert rendered == expected


def test_markers_have_no_empty_or_doubled_segments(sutra):
    for name, marker in _markers(sutra).items():
        assert marker.startswith("[sutra: "), name
        assert marker.endswith("]"), name
        assert marker == marker.strip(), name
        assert "· ·" not in marker, name
        assert "  " not in marker, name
        assert " ·]" not in marker, name
        assert "· id=" not in marker.replace(" · id=", ""), name
        assert marker.count(" · id=") in {0, 1}, name
        assert marker.count("[sutra:") == 1, name


def test_rewritten_output_carries_the_documented_marker(sutra):
    """The shapes a model actually reads, not just the builder's return value."""
    plain = sutra.compress_result(big())
    line = next(ln for ln in plain.splitlines() if ln.startswith("[sutra:"))
    assert re.fullmatch(
        r'\[sutra: partial · [\d,]+ bytes elided \(large output\) · '
        r'id=su_[0-9a-f]{32} · original [\d,]+ B retained(?: \d+h)? · '
        r'retrieve: sutra_restore\(id="su_[0-9a-f]{32}", mode="bytes", '
        r'start=\d+, end=\d+\)\]', line), line
    assert "· ·" not in line

    field = json.loads(sutra.recovery.restore(
        {"id": ID_RE.search(plain).group(1), "mode": "bytes"}))
    assert field["status"] == "ok"  # the marker's call is the documented one

    document = sutra.compress_result(json.dumps({"output": big(3_000)}))
    inner = json.loads(document)["output"]
    field_line = next(ln for ln in inner.splitlines() if ln.startswith("[sutra:"))
    assert ' · field="/output" · id=su_' in field_line, field_line
    assert "· ·" not in field_line

    diff = ("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
            + big())
    protected = sutra.compress_result(diff)
    assert protected == diff and "[sutra:" not in protected  # no marker at all


def test_oversized_json_document_is_compressed_and_round_trips(sutra, monkeypatch):
    """Regression: a small budget left every field unlabelled, so an oversized
    document came back at full size instead of compressed and recoverable."""
    monkeypatch.setenv("SUTRA_MAX_BYTES", "2000")
    document = {f"f{i}": big(250) for i in range(4)}
    payload = json.dumps(document)
    out = sutra.compress_result(payload)
    assert len(out.encode("utf-8")) <= 2_000
    parsed = json.loads(out)
    assert set(parsed) == set(document)
    ident = ID_RE.search(out).group(1)
    for key, original_text in document.items():
        kwargs = _bind(_calls(parsed[key])[0])
        assert kwargs["field"] == f"/{key}"
        kwargs["max_bytes"] = 65_536
        response = json.loads(sutra.recovery.handle_tool_call(kwargs))
        assert response["status"] == "ok"
        original = original_text.encode("utf-8")
        start, end = kwargs["start"], kwargs["end"]
        assert response["content"] == original[start:end].decode("utf-8", "replace")
    full = json.loads(sutra.recovery.handle_tool_call(
        {"id": ident, "mode": "full", "max_bytes": 65_536}))
    assert full["status"] == "ok"
    assert full["sha256_verified"] is True
    assert full["content"] == payload


# --- failure-matrix rows verified end to end (2026-10-24 audit) --------------

def test_a_write_that_pushes_the_store_over_quota_is_pruned_and_reports_not_retained(
        sutra, tmp_path, monkeypatch):
    """Failure matrix: quota exceeded after write. An entry that fits the per-entry
    cap but not the total quota is written, immediately swept, and `put` returns
    None — so the adapter deletes it again and the marker claims no id."""
    store = make_store(sutra, directory=tmp_path / "store", max_bytes=500,
                       max_entry_bytes=1_000, max_entries=100)
    assert store.put("x" * 600) is None
    assert store.usage() == (0, 0)
    # Through the adapter: no id may be promised for bytes that were not kept.
    monkeypatch.setattr(sutra._config, "resolve", lambda: sutra._config.Config(
        enabled=True, max_bytes=1_200, head_bytes=500, tail_bytes=200, json_items=0,
        stats_path=str(tmp_path / "stats.jsonl"), stats_max_bytes=0,
        store_dir=str(tmp_path / "store"), store_enabled=True, store_ttl_hours=1,
        store_max_bytes=500, store_max_entry_bytes=1_000, store_max_entries=100,
        recovery_max_bytes=8_192, recovery_max_matches=5))
    out = sutra.compress_result("y" * 2_000, tool_name="terminal")
    assert "original not retained" in out
    assert "id=" not in out


def test_restore_max_bytes_argument_is_clamped_both_ways(sutra, store):
    """Failure matrix: oversized restore requests. A caller-supplied max_bytes is
    clamped to the 64 KiB hard cap above and the 512 B floor below — no argument
    can buy an unbounded response or starve one."""
    entry = store.put("z" * 8_000)
    huge = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "bytes", "start": 0, "end": 8_000,
         "max_bytes": 10_000_000}, store=store))
    assert huge["status"] == "ok"
    assert huge["returned_bytes"] <= 65_536
    tiny = json.loads(sutra.recovery.restore(
        {"id": entry.id, "mode": "bytes", "start": 0, "end": 8_000,
         "max_bytes": 1}, store=store))
    assert tiny["status"] == "ok"
    assert 0 < tiny["returned_bytes"] <= 512
    assert tiny["more"] is True and tiny["next"] is not None


def test_disabled_store_reports_unavailable_for_a_known_id_shape(sutra, tmp_path,
                                                                 monkeypatch):
    """Failure matrix: store disabled. A syntactically valid id against a disabled
    store answers `unavailable` — the agent is told retention is off, not that the
    id was wrong."""
    monkeypatch.setenv("SUTRA_STORE", "0")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    response = json.loads(sutra.recovery.restore({"id": "su_" + "ab" * 16}))
    assert response["status"] == "unavailable"
    assert "SUTRA_STORE" in response["note"]
