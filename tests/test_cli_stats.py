"""Tests for `sutra stats`, the aggregator over the durable savings log.

The log is written by ``metrics.record_compression`` and by nothing else, so
these tests drive the writer and read the report back through the same schema:
a hand-built JSON line would prove the reader against a shape the writer never
produces.
"""

import json
import os
import types

from conftest import plugin_module

cli = plugin_module("cli")
metrics = plugin_module("metrics")


def _cfg(path, limit=10_000_000):
    return types.SimpleNamespace(stats_path=str(path), stats_max_bytes=limit)


def _write(path, **overrides):
    stats = {
        "changed": True,
        "tool": "Bash",
        "rule": "traceback",
        "original_bytes": 1_000,
        "compressed_bytes": 100,
        "omitted_bytes": 0,
        "partial": False,
        "fail_open": False,
        "duration_ms": 1.0,
    }
    stats.update(overrides)
    metrics.record_compression(stats, _cfg(path))
    return path


def test_the_log_aggregates_records_rules_and_tools(tmp_path):
    path = tmp_path / "stats.jsonl"
    _write(path, tool="Bash", rule="traceback", original_bytes=1_000, compressed_bytes=100)
    _write(path, tool="Read", rule="json", original_bytes=500, compressed_bytes=400)

    report = metrics.read_savings_log(str(path))

    assert report["records"] == 2
    assert report["original_bytes"] == 1_500
    assert report["compressed_bytes"] == 500
    assert report["saved_bytes"] == 1_000
    assert report["saved_pct"] == 66.7
    assert report["by_rule"]["traceback"] == {"records": 1, "saved_bytes": 900}
    assert report["by_rule"]["json"] == {"records": 1, "saved_bytes": 100}
    assert report["by_tool"]["Bash"] == {"records": 1, "saved_bytes": 900}
    assert report["first_at"] and report["last_at"]


def test_the_rotated_file_is_read_too(tmp_path):
    path = tmp_path / "stats.jsonl"
    _write(path, original_bytes=1_000, compressed_bytes=100)
    os.replace(path, str(path) + ".1")
    _write(path, original_bytes=2_000, compressed_bytes=1_000)

    report = metrics.read_savings_log(str(path))

    assert report["records"] == 2
    assert report["saved_bytes"] == 1_900
    assert report["files"] == [str(path) + ".1", str(path)]


def test_a_missing_log_is_zero_records_not_an_error(tmp_path):
    report = metrics.read_savings_log(str(tmp_path / "absent.jsonl"))

    assert report["records"] == 0
    assert report["files"] == []
    assert report["saved_pct"] == 0.0


def test_an_unreadable_line_is_counted_and_the_rest_still_parses(tmp_path):
    path = tmp_path / "stats.jsonl"
    _write(path, original_bytes=1_000, compressed_bytes=100)
    with open(path, "ab") as handle:
        handle.write(b"{not json\n")

    report = metrics.read_savings_log(str(path))

    assert report["records"] == 1
    assert report["unreadable"] == 1
    assert report["saved_bytes"] == 900


def test_a_number_that_is_not_a_number_counts_as_unreadable(tmp_path):
    path = tmp_path / "stats.jsonl"
    _write(path, original_bytes=1_000, compressed_bytes=100)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"rule": "json", "saved_bytes": "not a number"}) + "\n")

    report = metrics.read_savings_log(str(path))

    assert report["unreadable"] == 1, "a parsed line with junk numbers is not a record"
    assert report["records"] == 1
    assert report["saved_bytes"] == 900


def test_the_cli_reports_json_from_the_configured_path(tmp_path, monkeypatch, capsys):
    path = tmp_path / "stats.jsonl"
    _write(path, original_bytes=1_000, compressed_bytes=100)
    monkeypatch.setenv("SUTRA_STATS_FILE", str(path))

    assert cli.main(["stats", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["records"] == 1
    assert payload["saved_bytes"] == 900
    assert payload["path"] == str(path)


def test_the_cli_explains_a_missing_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUTRA_STATS_FILE", str(tmp_path / "absent.jsonl"))

    assert cli.main(["stats"]) == 0

    out = capsys.readouterr().out
    assert "no tool-result rewrites in" in out
    assert "no records yet" in out


def test_the_human_report_states_what_it_cannot_see(tmp_path, monkeypatch, capsys):
    path = tmp_path / "stats.jsonl"
    _write(path, tool="Bash", rule="traceback", original_bytes=1_000, compressed_bytes=100)

    assert cli.main(["stats", "--file", str(path)]) == 0

    out = capsys.readouterr().out
    assert "1 rewrite" in out or "rewrites: 1" in out
    assert "900 saved" in out
    assert "host process" in out
    assert "the request-stage rewrites beside it" in out


def test_the_file_flag_wins_over_the_environment(tmp_path, monkeypatch, capsys):
    configured = tmp_path / "configured.jsonl"
    explicit = tmp_path / "explicit.jsonl"
    _write(configured, original_bytes=1_000, compressed_bytes=100)
    _write(explicit, original_bytes=4_000, compressed_bytes=1_000)
    monkeypatch.setenv("SUTRA_STATS_FILE", str(configured))

    assert cli.main(["stats", "--file", str(explicit), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(explicit)
    assert payload["saved_bytes"] == 3_000
