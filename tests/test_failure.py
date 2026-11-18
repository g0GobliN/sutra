"""Tests for the scoped failure ledger."""

from __future__ import annotations

import json

from conftest import load_plugin, plugin_module

load_plugin()
failure = plugin_module("failure")
optimize = plugin_module("optimize")


def test_shape_is_cheap_and_honest():
    assert failure.shape('{"a": 1}') == "json"
    assert failure.shape("[1, 2]") == "json"
    assert failure.shape("diff --git a/x b/x\n") == "diff"
    assert failure.shape("Traceback (most recent call last):\n") == "traceback"
    assert failure.shape("def f():\n    return 1\n") == "code"
    assert failure.shape("plain words\n") == "text"
    assert failure.shape("") == "empty"


def test_fingerprint_is_stable_and_short():
    a = failure.fingerprint("text")
    assert a == failure.fingerprint("text")
    assert a != failure.fingerprint("other")
    assert len(a) == 16


def test_hard_failure_classification():
    assert failure.is_hard_failure("strategy raised: ValueError")
    assert failure.is_hard_failure("validator raised: KeyError")
    assert not failure.is_hard_failure("saved 0 bytes, below the 1-byte minimum")
    assert not failure.is_hard_failure("validator rejected: lost path")


def test_disables_after_three_scoped_failures():
    ledger = failure.Ledger()
    for _ in range(2):
        ledger.record(strategy="s", scope="text", text_fingerprint="f",
                      accepted=False, saved_bytes=0, elapsed_ms=1.0,
                      reason="strategy raised: boom")
    assert not ledger.is_disabled("s", "text")
    ledger.record(strategy="s", scope="text", text_fingerprint="f",
                  accepted=False, saved_bytes=0, elapsed_ms=1.0,
                  reason="strategy raised: boom")
    assert ledger.is_disabled("s", "text")
    assert not ledger.is_disabled("s", "json")   # scoped, not global
    assert not ledger.is_disabled("other", "text")


def test_success_resets_the_count():
    ledger = failure.Ledger()
    for _ in range(3):
        ledger.record(strategy="s", scope="text", text_fingerprint="f",
                      accepted=False, saved_bytes=0, elapsed_ms=1.0,
                      reason="strategy raised: boom")
    assert ledger.is_disabled("s", "text")
    ledger.record(strategy="s", scope="text", text_fingerprint="f",
                  accepted=True, saved_bytes=5, elapsed_ms=1.0)
    assert not ledger.is_disabled("s", "text")


def test_rejections_are_not_failures():
    ledger = failure.Ledger()
    for _ in range(5):
        ledger.record(strategy="s", scope="text", text_fingerprint="f",
                      accepted=False, saved_bytes=0, elapsed_ms=1.0,
                      reason="saved 0 bytes, below the 1-byte minimum")
    assert not ledger.is_disabled("s", "text")
    assert ledger.stats()["outcomes"]["rejected"] == 5


def test_reset_reenables():
    ledger = failure.Ledger()
    for _ in range(3):
        ledger.record(strategy="s", scope="text", text_fingerprint="f",
                      accepted=False, saved_bytes=0, elapsed_ms=1.0,
                      reason="candidate is empty")
    assert ledger.is_disabled("s", "text")
    ledger.reset()
    assert not ledger.is_disabled("s", "text")


def test_bounded_history_and_scopes():
    ledger = failure.Ledger(max_records=5, max_scopes=3)
    for i in range(10):
        ledger.record(strategy=f"s{i}", scope="text", text_fingerprint="f",
                      accepted=False, saved_bytes=0, elapsed_ms=1.0,
                      reason="candidate is empty")
    assert ledger.stats()["records"] == 5
    assert len(ledger.recent(100)) == 5


def test_durable_sink_writes_jsonl(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = failure.Ledger(path=str(path))
    ledger.record(strategy="s", scope="text", text_fingerprint="f",
                  accepted=True, saved_bytes=3, elapsed_ms=0.5)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["strategy"] == "s"
    assert rows[0]["outcome"] == "accepted"


def test_unwritable_sink_never_raises(tmp_path):
    ledger = failure.Ledger(path=str(tmp_path / "missing-dir" / "x" / "ledger.jsonl"))
    # The parent cannot be created as a file, so creation fails; no exception.
    (tmp_path / "missing-dir").write_text("not a directory")
    ledger.record(strategy="s", scope="text", text_fingerprint="f",
                  accepted=False, saved_bytes=0, elapsed_ms=1.0,
                  reason="candidate is empty")


def test_optimize_skips_a_disabled_strategy():
    ledger = failure.Ledger()

    def boom(text: str) -> str:
        raise RuntimeError("boom")

    strategy = optimize.Strategy("boom", boom)
    text = "line1\nline2\n"
    for _ in range(3):
        optimize.optimize(text, strategies=[strategy], ledger=ledger)
    assert ledger.is_disabled("boom", failure.shape(text))
    report = optimize.optimize(text, strategies=[strategy], ledger=ledger)
    skipped = [e for e in report.evaluations if e.name == "boom"]
    assert skipped and "skipped" in skipped[0].reason
    assert report.text == text


def test_record_attempts_feeds_a_run_report():
    report = optimize.optimize("line1  \nline2   \n")
    ledger = failure.Ledger()
    failure.record_attempts(ledger, "line1  \nline2   \n", report.evaluations)
    assert ledger.stats()["records"] == len(report.evaluations)
    assert ledger.recent(1)[0]["strategy"] in {s.name for s in optimize.default_strategies()}


def test_records_carry_sizes_and_the_validation_outcome():
    text = "line1  \nline2   \n"
    report = optimize.optimize(text)
    ledger = failure.Ledger()
    failure.record_attempts(ledger, text, report.evaluations)
    record = ledger.recent(1)[0]
    assert record["size_before"] == len(text.encode("utf-8"))
    assert record["size_after"] == record["size_before"] - record["saved_bytes"]
    assert record["validation"] == "passed" or record["validation"].startswith("rejected:")
    assert record["fingerprint"] == failure.fingerprint(text)
