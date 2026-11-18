"""Tests for the dependency-aware redundancy classifier (goals.md PART 7)."""

import json

import pytest

import redundancy


def test_verification_commands_are_never_skipped():
    for command in ("pytest -q", "npm test", "cargo test --all", "ruff check .",
                    "mypy src", "npm audit", "make verify", "./verify-all",
                    "git checkout main"):
        assert redundancy.mandatory_command(command) is (
            command != "git checkout main"), command


def test_mandatory_check_wins_over_a_fresh_record(tmp_path):
    record = tmp_path / "ledger.json"
    ledger = redundancy.Ledger(record)
    key = redundancy.call_fingerprint("Bash", "pytest -q")
    ledger.record(key, result_fingerprint="abc")
    decision = ledger.classify(key, command="pytest -q")
    assert decision.classification == redundancy.MUST_EXECUTE
    assert "never skipped" in decision.reason
    assert decision.reusable is False


def test_unknown_when_nothing_was_recorded(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    decision = ledger.classify("nope")
    assert decision.classification == redundancy.UNKNOWN
    assert decision.reusable is False


def test_recorded_call_with_unchanged_dependencies_is_safe(tmp_path):
    watched = tmp_path / "input.txt"
    watched.write_text("hello", encoding="utf-8")
    current = redundancy.snapshot([watched], root=tmp_path)
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    key = redundancy.call_fingerprint("Read", str(watched))
    ledger.record(key, dependencies=current, result_fingerprint="digest")
    decision = ledger.classify(key, dependencies=current)
    assert decision.classification == redundancy.SAFE_TO_REUSE
    assert decision.reusable is True
    assert decision.changed == ()


def test_changed_dependency_requires_revalidation(tmp_path):
    watched = tmp_path / "input.txt"
    watched.write_text("hello", encoding="utf-8")
    before = redundancy.snapshot([watched], root=tmp_path)
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    key = redundancy.call_fingerprint("Read", str(watched))
    ledger.record(key, dependencies=before)
    watched.write_text("hello again", encoding="utf-8")
    after = redundancy.snapshot([watched], root=tmp_path)
    decision = ledger.classify(key, dependencies=after)
    assert decision.classification == redundancy.REQUIRES_REVALIDATION
    assert decision.changed == ("input.txt",)
    assert "input.txt" in decision.reason


def test_version_stamp_change_requires_revalidation(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    key = "call"
    ledger.record(key, dependencies=redundancy.Snapshot(versions={"tool": "1.0"}))
    decision = ledger.classify(
        key, dependencies=redundancy.Snapshot(versions={"tool": "1.1"}))
    assert decision.classification == redundancy.REQUIRES_REVALIDATION
    assert decision.changed == ("tool",)


def test_expired_record_requires_revalidation(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json", ttl_seconds=60)
    key = "call"
    ledger.record(key, now=1_000.0)
    assert ledger.classify(key, now=1_030.0).classification == redundancy.SAFE_TO_REUSE
    decision = ledger.classify(key, now=1_061.0)
    assert decision.classification == redundancy.REQUIRES_REVALIDATION
    assert "expired" in decision.reason


def test_a_negative_ttl_never_expires(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json", ttl_seconds=-1)
    ledger.record("call", now=0.0)
    assert ledger.classify("call", now=10**9).reusable is True


def test_unreadable_ledger_fails_open(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    ledger = redundancy.Ledger(path)
    assert len(ledger) == 0
    assert ledger.classify("x").classification == redundancy.UNKNOWN
    ledger.record("x")
    assert ledger.classify("x").reusable is True


def test_wrong_version_is_ignored(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"version": 99, "entries": {"x": {}}}),
                    encoding="utf-8")
    assert len(redundancy.Ledger(path)) == 0


def test_persisted_between_instances(tmp_path):
    path = tmp_path / "nested" / "ledger.json"
    first = redundancy.Ledger(path)
    key = redundancy.call_fingerprint("Read", "a")
    first.record(key, result_fingerprint="r")
    second = redundancy.Ledger(path)
    assert second.entry(key)["result"] == "r"
    assert second.classify(key).reusable is True


def test_eviction_keeps_the_newest_entries(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json", max_entries=3)
    for index in range(6):
        ledger.record(f"call-{index}", now=float(index))
    assert len(ledger) == 3
    assert ledger.entry("call-5") is not None
    assert ledger.entry("call-0") is None


def test_forget_removes_one_entry(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    ledger.record("a")
    assert ledger.forget("a") is True
    assert ledger.forget("a") is False
    assert ledger.classify("a").classification == redundancy.UNKNOWN


def test_fingerprint_is_stable_and_argument_sensitive():
    first = redundancy.call_fingerprint("Read", '{"path": "a"}')
    assert first == redundancy.call_fingerprint("Read", '{"path": "a"}')
    assert first != redundancy.call_fingerprint("Read", '{"path": "b"}')
    assert len(first) == 64


def test_missing_file_is_reported_not_assumed(tmp_path):
    current = redundancy.snapshot([tmp_path / "absent.txt"], root=tmp_path)
    assert list(current.files.values()) == ["missing"]
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    ledger.record("call", dependencies=current)
    assert ledger.classify("call", dependencies=current).reusable is True
    assert "absent.txt" in current.files


def test_large_files_are_stamped_without_hashing(tmp_path, monkeypatch):
    monkeypatch.setattr(redundancy, "MAX_FILE_HASH_BYTES", 4)
    watched = tmp_path / "big.txt"
    watched.write_bytes(b"0123456789")
    current = redundancy.snapshot([watched], root=tmp_path)
    assert current.files["big.txt"].endswith("unhashed")


def test_cli_check_and_record(tmp_path, capsys):
    from conftest import plugin_module
    cli = plugin_module("cli")
    ledger_path = tmp_path / "ledger.json"
    key = redundancy.call_fingerprint("Bash", "pytest -q")
    assert cli.main(["redundancy", "check", "--key", key,
                     "--file", str(ledger_path), "--json"]) == 1
    assert cli.main(["redundancy", "record", "--key", key, "--result",
                     "digest", "--file", str(ledger_path)]) == 0
    capsys.readouterr()
    assert cli.main(["redundancy", "check", "--key", key,
                     "--file", str(ledger_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["classification"] == redundancy.SAFE_TO_REUSE
    assert cli.main(["redundancy", "check", "--key", key,
                     "--command", "pytest -q", "--file", str(ledger_path)]) == 1
    assert "MUST_EXECUTE" in capsys.readouterr().out
    assert cli.main(["redundancy", "check", "--file", str(ledger_path)]) == 2


def test_a_path_list_is_rejected_instead_of_crashing_later(tmp_path):
    ledger = redundancy.Ledger(tmp_path / "ledger.json")
    with pytest.raises(TypeError, match="needs a Snapshot"):
        ledger.record("k", dependencies=["a.py"])
    ledger.record("k")
    with pytest.raises(TypeError, match="needs a Snapshot"):
        ledger.classify("k", dependencies=["a.py"])
