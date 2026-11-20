"""The one surface that can skip work: reuse before the call runs.

Every other SUTRA surface sees a result after the work happened. These tests
pin the *decision*, not just the plumbing: the same call must execute when a
dependency changed and be reused when it did not, so a regression that turns
reuse into an unconditional cache fails here rather than in production.
"""

import json

import pytest


@pytest.fixture()
def store(tmp_path, sutra):
    return sutra.replay.Store(tmp_path / "replay.json", ttl_seconds=600,
                              max_entries=4)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point the middleware at this test's store, not the real one."""
    monkeypatch.setenv("SUTRA_REPLAY_FILE", str(tmp_path / "middleware.json"))
    monkeypatch.setenv("SUTRA_STATS_FILE", str(tmp_path / "stats.jsonl"))
    monkeypatch.setenv("SUTRA_STORE_DIR", str(tmp_path / "store"))
    return tmp_path


# -- the decision -----------------------------------------------------------


def test_second_read_of_an_unchanged_file_is_reused(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("first version\n", encoding="utf-8")

    first = store.lookup("read_file", {"path": str(target)}, session_id="s1")
    assert isinstance(first, sutra.replay.Miss), first.reason

    assert store.remember("read_file", {"path": str(target)}, "first version\n",
                          session_id="s1") is True

    hit = store.lookup("read_file", {"path": str(target)}, session_id="s1")
    assert isinstance(hit, sutra.replay.Hit), hit.reason
    assert hit.value == "first version\n"


def test_a_changed_file_forces_execution(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("first version\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target)}, "first version\n",
                   session_id="s1")

    target.write_text("second version\n", encoding="utf-8")

    decision = store.lookup("read_file", {"path": str(target)}, session_id="s1")
    assert isinstance(decision, sutra.replay.Miss)
    assert "dependencies changed" in decision.reason


def test_same_size_rewrite_is_detected(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("aaaa\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target)}, "aaaa\n", session_id="s1")

    target.write_text("bbbb\n", encoding="utf-8")

    decision = store.lookup("read_file", {"path": str(target)}, session_id="s1")
    assert isinstance(decision, sutra.replay.Miss)


def test_expired_entry_forces_execution(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target)}, "body\n",
                   session_id="s1", now=0.0)

    decision = store.lookup("read_file", {"path": str(target)},
                            session_id="s1", now=601.0)
    assert isinstance(decision, sutra.replay.Miss)
    assert "expired" in decision.reason


def test_a_different_session_never_reuses(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target)}, "body\n", session_id="s1")

    decision = store.lookup("read_file", {"path": str(target)}, session_id="s2")
    assert isinstance(decision, sutra.replay.Miss)


def test_no_session_never_reuses(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target)}, "body\n", session_id="s1")

    decision = store.lookup("read_file", {"path": str(target)}, session_id=None)
    assert isinstance(decision, sutra.replay.Miss)


def test_argument_order_does_not_defeat_reuse(store, sutra, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target), "encoding": "utf-8"},
                   "body\n", session_id="s1")

    hit = store.lookup("read_file", {"encoding": "utf-8", "path": str(target)},
                       session_id="s1")
    assert isinstance(hit, sutra.replay.Hit), hit.reason


# -- what must never be reused ---------------------------------------------


@pytest.mark.parametrize("tool", ["write_file", "edit", "bash", "run_tests",
                                 "delete_path", "install", "apply_patch",
                                 "search_and_replace"])
def test_mutating_tools_are_never_reused(store, sutra, tmp_path, tool):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    assert store.remember(tool, {"path": str(target)}, "body\n",
                          session_id="s1") is False
    assert isinstance(store.lookup(tool, {"path": str(target)}, session_id="s1"),
                      sutra.replay.Miss)


def test_a_shell_command_is_never_reused(store, sutra):
    decision = store.lookup("bash", {"command": "pytest -q"}, session_id="s1")
    assert isinstance(decision, sutra.replay.Miss)
    assert "not a read-only inspection tool" in decision.reason


def test_remote_content_is_never_reused(store, sutra):
    decision = store.lookup("read_url", {"url": "https://example.com/data"},
                            session_id="s1")
    assert isinstance(decision, sutra.replay.Miss)


def test_a_call_without_a_path_is_never_reused(store, sutra):
    decision = store.lookup("read_file", {"line": 3}, session_id="s1")
    assert isinstance(decision, sutra.replay.Miss)
    assert "no existing file" in decision.reason


def test_a_directory_is_never_reused(store, sutra, tmp_path):
    """A directory stamp does not cover the files inside it."""
    (tmp_path / "inside.txt").write_text("one\n", encoding="utf-8")
    decision = store.lookup("grep", {"path": str(tmp_path), "pattern": "one"},
                            session_id="s1")
    assert isinstance(decision, sutra.replay.Miss)
    assert "not cover its files" in decision.reason
    assert store.remember("grep", {"path": str(tmp_path), "pattern": "one"},
                          "inside.txt: one", session_id="s1") is False


def test_a_missing_path_is_never_recorded(store, tmp_path):
    absent = tmp_path / "not-there.txt"
    assert store.remember("read_file", {"path": str(absent)}, "x",
                          session_id="s1") is False


def test_an_oversized_result_is_not_stored(sutra, tmp_path):
    small = sutra.replay.Store(tmp_path / "bounded.json", max_result_bytes=1024)
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    assert small.remember("read_file", {"path": str(target)}, "x" * 4096,
                          session_id="s1") is False


def test_the_store_stays_bounded(sutra, tmp_path):
    small = sutra.replay.Store(tmp_path / "bounded.json", max_entries=2)
    for index in range(4):
        target = tmp_path / f"file{index}.txt"
        target.write_text("body\n", encoding="utf-8")
        small.remember("read_file", {"path": str(target)}, "body\n",
                       session_id="s1")
    assert len(small) == 2


def test_a_corrupt_store_file_is_ignored(sutra, tmp_path):
    path = tmp_path / "replay.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(sutra.replay.Store(path)) == 0


def test_entries_from_another_version_are_dropped(sutra, tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"version": 99, "entries": {"x": {"result": 1}}}),
                    encoding="utf-8")
    assert len(sutra.replay.Store(path)) == 0


def test_a_failed_write_does_not_raise(sutra, tmp_path, monkeypatch):
    store = sutra.replay.Store(tmp_path / "sub" / "replay.json")
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sutra.replay.os, "replace", boom)
    # The in-memory entry is kept; a persistence failure never fails the call.
    assert store.remember("read_file", {"path": str(target)}, "body\n",
                          session_id="s1") is True


# -- the middleware itself -------------------------------------------------


def _counting(next_value):
    calls = {"n": 0}

    def next_call(_args):
        calls["n"] += 1
        return next_value

    return next_call, calls


def test_middleware_skips_the_second_identical_read(sutra, home, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("read me\n", encoding="utf-8")
    next_call, calls = _counting("read me\n")

    first = sutra.tool_execution(next_call=next_call, tool_name="read_file",
                                args={"path": str(target)}, session_id="s1")
    second = sutra.tool_execution(next_call=next_call, tool_name="read_file",
                                 args={"path": str(target)}, session_id="s1")

    assert calls["n"] == 1, "the second identical read must not execute"
    assert first == second == "read me\n"


def test_middleware_executes_again_after_the_file_changes(sutra, home, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("first\n", encoding="utf-8")
    next_call, calls = _counting("first\n")
    sutra.tool_execution(next_call=next_call, tool_name="read_file",
                         args={"path": str(target)}, session_id="s1")

    target.write_text("second\n", encoding="utf-8")
    fresh_call, fresh = _counting("second\n")
    result = sutra.tool_execution(next_call=fresh_call, tool_name="read_file",
                                  args={"path": str(target)}, session_id="s1")

    assert fresh["n"] == 1, "changed dependencies must execute"
    assert result == "second\n"


def test_middleware_never_reuses_a_write(sutra, home, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    next_call, calls = _counting({"ok": True})

    for _ in range(2):
        sutra.tool_execution(next_call=next_call, tool_name="write_file",
                             args={"path": str(target)}, session_id="s1")

    assert calls["n"] == 2


def test_middleware_replays_the_bytes_it_served(sutra, home, tmp_path):
    """A replay returns exactly what the host returned the first time."""
    target = tmp_path / "big.txt"
    body = "\n".join(f"line {index}" for index in range(40000))
    target.write_text(body, encoding="utf-8")
    next_call, calls = _counting(body)

    first = sutra.tool_execution(next_call=next_call, tool_name="read_file",
                                 args={"path": str(target)}, session_id="s1")
    second = sutra.tool_execution(next_call=next_call, tool_name="read_file",
                                  args={"path": str(target)}, session_id="s1")

    assert calls["n"] == 1
    assert first == second


def test_replay_is_off_in_off_mode(sutra, monkeypatch, tmp_path):
    monkeypatch.setenv("SUTRA_MODE", "off")
    monkeypatch.setenv("SUTRA_REPLAY_FILE", str(tmp_path / "off.json"))
    assert sutra.replay.store_for(sutra.config) is None


def test_replay_respects_the_disabled_switch(sutra, monkeypatch, tmp_path):
    monkeypatch.setenv("SUTRA_REPLAY", "0")
    monkeypatch.setenv("SUTRA_REPLAY_FILE", str(tmp_path / "disabled.json"))
    assert sutra.replay.store_for(sutra.config) is None


def test_stats_report_what_happened(store, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("body\n", encoding="utf-8")
    store.remember("read_file", {"path": str(target)}, "body\n", session_id="s1")
    store.lookup("read_file", {"path": str(target)}, session_id="s1")
    store.lookup("read_file", {"path": str(target)}, session_id="other")

    stats = store.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
