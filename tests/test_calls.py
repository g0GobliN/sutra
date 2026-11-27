"""The calls a session already made, and the seam a host asks before running one.

Ponytail's pre-execution reuse is an avoidance protocol, not a substitution
one: no host SUTRA integrates with lets a plugin hand back a result in place of
a call, so the seam answers "allow" or "deny with the id of the stored result".
These tests pin the identity of a call, the bounds of the index, the store
check that keeps a stale suggestion from being made, and the CLI contract.
"""

import io
import json
import os
import sys
import types

import pytest

from sutra import calls


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SUTRA_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("SUTRA_STORE_MAX_BYTES", str(8 * 1024 * 1024))
    return tmp_path / "store"


def store():
    from sutra import store as store_module

    return store_module.store_from_env()


def retain(text: str = "original bytes", *, session: str = "s1") -> str:
    entry = store().put(text, tool_name="read_file", session_id=session)
    assert entry is not None
    return entry.id


# -- identity ---------------------------------------------------------------


def test_a_key_ignores_the_order_of_the_arguments():
    one = calls.key("s1", "read_file", {"path": "a", "offset": 0})
    same = calls.key("s1", "read_file", {"offset": 0, "path": "a"})

    assert one == same


def test_a_key_separates_session_tool_and_arguments():
    base = calls.key("s1", "read_file", {"path": "a"})

    assert base != calls.key("s2", "read_file", {"path": "a"})
    assert base != calls.key("s1", "write_file", {"path": "a"})
    assert base != calls.key("s1", "read_file", {"path": "b"})
    assert base != calls.key("s1", "read_file", {"path": "a", "offset": 0})


def test_arguments_that_cannot_be_encoded_have_no_key():
    assert calls.key("s1", "read_file", object()) is None
    assert calls.key("s1", "read_file", {"size": float("nan")}) is None
    assert calls.key("", "read_file", {"path": "a"}) is None
    assert calls.key("s1", "", {"path": "a"}) is None


# -- the index --------------------------------------------------------------


def test_a_remembered_call_is_found_while_the_store_holds_it(isolated_store):
    entry_id = retain()

    assert calls.remember("s1", "read_file", {"path": "a"}, entry_id) is True
    assert calls.lookup("s1", "read_file", {"path": "a"}) == entry_id


def test_a_call_whose_entry_is_gone_is_not_suggested_again(isolated_store):
    entry_id = retain()
    calls.remember("s1", "read_file", {"path": "a"}, entry_id)

    store().delete(entry_id)

    assert calls.lookup("s1", "read_file", {"path": "a"}) is None
    assert json.loads((isolated_store / calls.FILE_NAME).read_text()) == {}


def test_the_index_keeps_a_bounded_number_of_calls(isolated_store, monkeypatch):
    monkeypatch.setattr(calls, "MAX_ENTRIES_PER_SESSION", 3)
    entry_id = retain()
    for index in range(5):
        calls.remember("s1", "read_file", {"path": f"a{index}"}, entry_id,
                       now=float(index))

    document = json.loads((isolated_store / calls.FILE_NAME).read_text())

    assert len(document["s1"]) == 3
    assert calls.lookup("s1", "read_file", {"path": "a0"}) is None
    assert calls.lookup("s1", "read_file", {"path": "a4"}) == entry_id


def test_an_index_that_cannot_be_read_suggests_nothing(isolated_store):
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / calls.FILE_NAME).write_text("{not json", encoding="utf-8")

    assert calls.lookup("s1", "read_file", {"path": "a"}) is None


def test_forgetting_a_call_that_was_never_remembered_is_harmless(isolated_store):
    calls.forget("s1", "read_file", {"path": "a"})

    assert not (isolated_store / calls.FILE_NAME).exists()


# -- the CLI seam -----------------------------------------------------------


def run(payload, monkeypatch):
    from sutra import cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    code = cli.main(["pre-execute"])
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None)


def test_the_seam_allows_a_call_it_has_never_seen(isolated_store, monkeypatch):
    code, report = run({"session_id": "s1", "tool_name": "read_file",
                        "arguments": {"path": "a"}}, monkeypatch)

    assert code == 0
    assert report == {"decision": "allow"}


def test_the_seam_denies_a_call_whose_result_is_stored(isolated_store, monkeypatch):
    entry_id = retain()
    calls.remember("s1", "read_file", {"path": "a"}, entry_id)

    code, report = run({"session_id": "s1", "tool_name": "read_file",
                        "arguments": {"path": "a"}}, monkeypatch)

    assert code == 0
    assert report["decision"] == "deny"
    assert report["id"] == entry_id
    assert entry_id in report["reason"]
    assert "sutra_restore" in report["reason"]


def test_the_seam_allows_a_call_with_a_changed_argument(isolated_store, monkeypatch):
    entry_id = retain()
    calls.remember("s1", "read_file", {"path": "a"}, entry_id)

    code, report = run({"session_id": "s1", "tool_name": "read_file",
                        "arguments": {"path": "b"}}, monkeypatch)

    assert code == 0
    assert report == {"decision": "allow"}


@pytest.mark.parametrize("payload", [
    {},
    {"tool_name": "read_file"},
    {"tool_name": "read_file", "arguments": {"path": "a"}},
    {"session_id": "s1", "arguments": {"path": "a"}},
    [],
])
def test_the_seam_allows_a_payload_it_cannot_use(payload, isolated_store, monkeypatch):
    code, report = run(payload, monkeypatch)

    assert code == 0
    assert report == {"decision": "allow"}


def test_the_seam_allows_a_call_when_the_payload_is_not_json(isolated_store, monkeypatch):
    from sutra import cli

    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    assert cli.main(["pre-execute"]) == 0
    assert json.loads(out.getvalue()) == {"decision": "allow"}


# -- the engine records the call -------------------------------------------


def payload_text() -> str:
    return "\n".join(f"build line {index} nominal" for index in range(2000))


def test_a_stored_rewrite_records_the_call_it_came_from(isolated_store):
    from sutra import core

    outcome = core.process_text(payload_text(), tool_name="read_file",
                                session_id="s1", arguments={"path": "a"})

    assert outcome.changed is True
    assert outcome.id
    assert calls.lookup("s1", "read_file", {"path": "a"}) == outcome.id


def test_a_rewrite_without_arguments_records_nothing(isolated_store):
    from sutra import core

    outcome = core.process_text(payload_text(), tool_name="read_file",
                                session_id="s1")

    assert outcome.changed is True
    assert calls.lookup("s1", "read_file", {"path": "a"}) is None


# -- only a read may be replaced by the bytes it already produced ----------


def test_only_an_inspection_tool_can_be_skipped():
    assert calls.reusable("read_file") is True
    assert calls.reusable("filesystem.read") is True
    assert calls.reusable("grep") is True
    assert calls.reusable("write_file") is False
    assert calls.reusable("bash") is False
    assert calls.reusable("read_write") is False


def test_a_write_tool_is_never_recorded_or_suggested(isolated_store):
    entry_id = retain()

    assert calls.remember("s1", "write_file", {"path": "a"}, entry_id) is False
    assert calls.lookup("s1", "write_file", {"path": "a"}) is None


def test_the_index_is_locked_down_where_fchmod_does_not_exist(isolated_store):
    target = isolated_store.parent / "calls.json"
    target.write_text("{}", encoding="utf-8")
    shim = types.SimpleNamespace(chmod=os.chmod)
    handle = os.open(target, os.O_RDONLY)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(calls, "os", shim)
            calls._lock_down(handle, str(target))
    finally:
        os.close(handle)


def test_a_result_with_several_texts_records_nothing(isolated_store):
    from sutra import core

    text = payload_text()
    result = {"content": [{"type": "text", "text": text},
                          {"type": "text", "text": text}]}

    core.process_result(result, tool_name="read_file", session_id="s1",
                        arguments={"path": "a"})

    assert calls.lookup("s1", "read_file", {"path": "a"}) is None


def test_the_cli_records_nothing_for_a_result_with_several_texts(
        isolated_store, monkeypatch):
    from sutra import cli

    payload = {"tool_name": "read_file", "session_id": "s1",
               "arguments": {"path": "a"},
               "texts": [payload_text(), payload_text()]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    assert cli.main(["process-result"]) == 0
    assert json.loads(out.getvalue())["changed"] is True
    assert calls.lookup("s1", "read_file", {"path": "a"}) is None


def test_a_seam_payload_without_an_arguments_field_is_allowed(
        isolated_store, monkeypatch):
    code, report = run({"session_id": "s1", "tool_name": "read_file"}, monkeypatch)

    assert code == 0
    assert report == {"decision": "allow"}
