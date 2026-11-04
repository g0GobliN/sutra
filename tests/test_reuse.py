"""Exact-duplicate reuse: verified ids, bounded index, fail-open behaviour."""

import json
import re

import pytest

from conftest import load_plugin, plugin_module

sutra = load_plugin()
reuse = plugin_module("reuse")
config = plugin_module("config")

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def _payload(lines=4000, clue="CLUE: reuse-token-7Q"):
    body = [f"line {index:05d} " + "padding " * 4 for index in range(lines)]
    body[max(0, lines - 100)] = clue
    return "\n".join(body) + "\n"


@pytest.fixture(autouse=True)
def reusable(monkeypatch):
    reuse.reset()
    monkeypatch.setenv("SUTRA_MAX_BYTES", "2000")
    monkeypatch.setenv("SUTRA_HEAD_BYTES", "500")
    monkeypatch.setenv("SUTRA_TAIL_BYTES", "300")
    monkeypatch.setenv("SUTRA_REUSE_MIN_BYTES", "1024")
    return monkeypatch


def _first(text, session="session-a", tool="read_file"):
    return sutra.process_text(text, tool_name=tool, session_id=session)


def test_an_exact_repeat_is_replaced_by_a_verified_marker():
    text = _payload()
    first = _first(text)
    assert first.changed and first.id
    assert "[sutra: partial" in first.output  # the marker sits between head and tail
    before = sutra.metrics_snapshot()["reuse"]["hits"]
    second = _first(text)
    assert second.changed
    assert second.output.startswith("[sutra: repeated output")
    assert f"id={first.id}" in second.output
    assert second.id == first.id
    assert second.compressed_bytes < first.compressed_bytes
    assert "sutra_restore" in second.output
    after = sutra.metrics_snapshot()["reuse"]["hits"]
    assert after == before + 1


def test_the_repeat_does_not_write_a_second_store_entry():
    text = _payload()
    first = _first(text)
    store = sutra._store.Store(sutra._store.StoreConfig.from_config(sutra.configuration()))
    entries, _ = store.usage()
    second = _first(text)
    assert second.id == first.id
    assert store.usage()[0] == entries == 1


def test_the_marker_id_restores_the_exact_original_bytes():
    text = _payload()
    _first(text)
    marker = _first(text).output
    entry_id = ID_RE.search(marker).group(1)
    response = json.loads(sutra.restore(
        {"id": entry_id, "mode": "bytes", "start": 0, "end": 8192}))
    assert response["status"] == "ok"
    assert response["content"] == text.encode("utf-8")[:8192].decode("utf-8")


def test_the_clue_is_still_recoverable_after_reuse():
    text = _payload()
    _first(text)
    marker = _first(text).output
    entry_id = ID_RE.search(marker).group(1)
    response = json.loads(sutra.restore(
        {"id": entry_id, "mode": "search", "query": "reuse-token-7Q"}))
    assert response["status"] == "ok"
    assert "reuse-token-7Q" in response["content"]


def test_another_session_has_its_own_index():
    text = _payload()
    first = _first(text, session="session-b")
    second = _first(text, session="session-c")
    assert second.changed
    assert not second.output.startswith("[sutra: repeated output")
    assert second.id != first.id


def test_a_payload_below_the_reuse_threshold_is_not_remembered():
    text = _payload(lines=10)  # below both the compression cap and the reuse floor
    first = _first(text, session="session-small")
    second = _first(text, session="session-small")
    assert not first.changed and not second.changed
    assert reuse.tracked("session-small") == 0


def test_disabled_reuse_keeps_compression_working(reusable):
    reusable.setenv("SUTRA_REUSE", "0")
    text = _payload()
    first = _first(text, session="session-disabled")
    second = _first(text, session="session-disabled")
    assert first.changed and second.changed
    assert "[sutra: partial" in second.output
    assert "[sutra: repeated output" not in second.output


def test_disabled_store_cannot_offer_reuse(reusable):
    reusable.setenv("SUTRA_STORE", "0")
    text = _payload()
    first = _first(text, session="session-nostore")
    second = _first(text, session="session-nostore")
    assert first.changed and first.id is None
    assert second.changed and not second.output.startswith("[sutra: repeated output")


def test_a_pruned_entry_falls_back_to_normal_compression():
    text = _payload()
    first = _first(text, session="session-pruned")
    store = sutra._store.Store(sutra._store.StoreConfig.from_config(sutra.configuration()))
    store.delete(first.id)
    second = _first(text, session="session-pruned")
    assert "[sutra: partial" in second.output
    assert "[sutra: repeated output" not in second.output
    assert second.id != first.id


def test_diffs_are_never_reused():
    patch = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n" + "+line\n" * 3000
    first = _first(patch, session="session-diff")
    second = _first(patch, session="session-diff")
    assert first.output == patch and second.output == patch
    assert reuse.tracked("session-diff") == 0


def test_json_documents_keep_their_shape_and_are_not_reused():
    document = json.dumps({"output": "row " * 5000, "status": "ok"})
    first = _first(document, session="session-json")
    second = _first(document, session="session-json")
    assert first.changed and second.changed
    assert second.output.startswith("{")
    assert not second.output.startswith("[sutra: repeated output")
    assert reuse.tracked("session-json") == 0


def test_a_dict_result_keeps_its_type_when_reused():
    text = _payload()
    first = sutra.process_result({"output": text}, tool_name="read_file",
                                 session_id="session-dict")
    second = sutra.process_result({"output": text}, tool_name="read_file",
                                  session_id="session-dict")
    assert isinstance(first, dict) and isinstance(second, dict)
    assert second["output"].startswith("[sutra: repeated output")
    assert "id=" in second["output"]


def test_the_index_is_bounded_per_session(reusable):
    cfg = config.resolve()
    for index in range(reuse.MAX_ENTRIES_PER_SESSION + 10):
        reuse.remember("x" * 2048 + str(index), session_id="session-bound",
                       entry_id=f"su_{index:032x}", visible_bytes=512, cfg=cfg)
    assert reuse.tracked("session-bound") == reuse.MAX_ENTRIES_PER_SESSION


def test_the_index_is_bounded_across_sessions(reusable):
    cfg = config.resolve()
    for index in range(reuse.MAX_SESSIONS + 5):
        reuse.remember("x" * 2048 + str(index), session_id=f"session-{index}",
                       entry_id=f"su_{index:032x}", visible_bytes=512, cfg=cfg)
    assert len(reuse._SEEN) == reuse.MAX_SESSIONS


def test_a_store_failure_fails_open(reusable, monkeypatch):
    text = _payload()
    _first(text, session="session-fail")

    def broken_get(self, entry_id, now=None):
        raise OSError("store vanished")

    monkeypatch.setattr(reuse._store.Store, "get", broken_get)
    second = _first(text, session="session-fail")
    assert second.changed
    assert "[sutra: partial" in second.output
    assert "[sutra: repeated output" not in second.output
