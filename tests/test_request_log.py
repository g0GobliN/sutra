"""The durable request-stage log: what it writes and who can read it."""

from __future__ import annotations

import json
import types

from conftest import plugin_module

metrics = plugin_module("metrics")
cli = plugin_module("cli")

ROOT = plugin_module("__init__").__file__.rsplit("/", 1)[0]


def config_at(path, *, limit=10_000_000):
    return types.SimpleNamespace(stats_path=str(path), stats_max_bytes=limit)


def decision(before, after):
    return types.SimpleNamespace(
        changed=after != before, bytes_before=before, bytes_after=after)


def request_log(path):
    return metrics.request_log_path(str(path))


def read_lines(path):
    with open(request_log(path), "rb") as handle:
        return [json.loads(line) for line in handle.read().splitlines() if line.strip()]


def test_a_shortened_request_is_written_with_its_session(tmp_path):
    path = tmp_path / "sutra-stats.jsonl"
    metrics.record_request_rewrite(
        decision(90_000, 30_000), mode="aggressive", session_id="local-abc",
        config=config_at(path))
    (record,) = read_lines(path)
    assert record["mode"] == "aggressive"
    assert record["session"] == "local-abc"
    assert record["before"] == 90_000
    assert record["after"] == 30_000
    assert record["saved"] == 60_000
    assert record["at"]


def test_a_request_left_alone_writes_nothing(tmp_path):
    path = tmp_path / "sutra-stats.jsonl"
    metrics.record_request_rewrite(
        decision(90_000, 90_000), mode="balanced", session_id="s1",
        config=config_at(path))
    assert not request_log(path).endswith("sutra-stats.jsonl")
    assert not __import__("os").path.exists(request_log(path))


def test_the_report_sums_modes_and_sessions(tmp_path):
    path = tmp_path / "sutra-stats.jsonl"
    for mode, session, before, after in (
            ("balanced", "local-a", 1_000, 900),
            ("balanced", "local-a", 2_000, 1_000),
            ("beast", "local-b", 4_000, 1_000)):
        metrics.record_request_rewrite(
            decision(before, after), mode=mode, session_id=session,
            config=config_at(path))
    report = metrics.read_request_log(str(path))
    assert report["records"] == 3
    assert report["before"] == 7_000
    assert report["after"] == 2_900
    assert report["saved"] == 4_100
    assert report["saved_pct"] == 58.6
    assert report["by_mode"]["balanced"] == {"records": 2, "saved": 1_100}
    assert report["by_mode"]["beast"] == {"records": 1, "saved": 3_000}
    assert report["by_session"]["local-a"] == {"records": 2, "saved": 1_100}
    assert report["files"] == [request_log(path)]


def test_the_rotated_request_log_is_read_too(tmp_path):
    import os

    path = tmp_path / "sutra-stats.jsonl"
    metrics.record_request_rewrite(decision(500, 400), mode="balanced",
                                   session_id="s1", config=config_at(path))
    os.replace(request_log(path), request_log(path) + ".1")
    metrics.record_request_rewrite(decision(300, 200), mode="balanced",
                                   session_id="s1", config=config_at(path))
    report = metrics.read_request_log(str(path))
    assert report["records"] == 2
    assert report["files"] == [request_log(path) + ".1", request_log(path)]


def test_an_unreadable_request_line_is_counted_not_raised(tmp_path):
    path = tmp_path / "sutra-stats.jsonl"
    metrics.record_request_rewrite(decision(500, 400), mode="balanced",
                                   session_id="s1", config=config_at(path))
    with open(request_log(path), "ab") as handle:
        handle.write(b"{not json\n")
    report = metrics.read_request_log(str(path))
    assert report["records"] == 1
    assert report["unreadable"] == 1


def test_the_stats_command_reports_the_request_stage(tmp_path, capsys):
    path = tmp_path / "sutra-stats.jsonl"
    metrics.record_request_rewrite(decision(10_000, 4_000), mode="aggressive",
                                   session_id="local-xyz", config=config_at(path))
    assert cli.main(["stats", "--file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "request stage: 1 rewrite(s), 6,000 bytes removed (60.0%)" in out
    assert "by mode aggressive: 1 rewrite(s), 6,000 bytes removed" in out


def test_the_stats_json_carries_the_request_report(tmp_path, capsys):
    path = tmp_path / "sutra-stats.jsonl"
    metrics.record_request_rewrite(decision(10_000, 4_000), mode="aggressive",
                                   session_id="local-xyz", config=config_at(path))
    assert cli.main(["stats", "--file", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["request_stage"]["records"] == 1
    assert report["request_stage"]["by_session"]["local-xyz"]["saved"] == 6_000


def test_an_empty_request_log_is_named_not_an_error(tmp_path, capsys):
    path = tmp_path / "sutra-stats.jsonl"
    assert cli.main(["stats", "--file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "no rewrite recorded" in out
