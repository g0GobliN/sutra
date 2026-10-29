"""The benchmark's numbers are a claim in the README, so CI checks the claim."""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "sutra_bench", os.path.join(ROOT, "bench", "benchmark.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_every_case_is_measured_and_never_grows():
    rows = bench.measure()
    assert len(rows) == 8
    for row in rows:
        assert row["before_bytes"] > 0
        assert row["after_bytes"] <= row["before_bytes"], row["case"]
        assert 0.0 <= row["saved_pct"] <= 100.0


def test_the_patch_case_is_left_alone():
    """A compressor that shrinks diffs can hide a code change."""
    rows = {r["case"]: r for r in bench.measure()}
    patch = rows["600 KB patch"]
    assert patch["rule"] == "diff-kept"
    assert patch["saved_bytes"] == 0


def test_a_json_case_comes_back_as_valid_json():
    rows = {r["case"]: r for r in bench.measure()}
    assert rows["JSON tool envelope"]["rule"] == "json-fields"


def test_the_json_array_case_keeps_its_shape():
    rows = {r["case"]: r for r in bench.measure()}
    row = rows["8,000-item JSON array"]
    assert row["rule"] == "json-kept"
    assert row["saved_bytes"] == 0


def test_every_buried_clue_is_recovered():
    rows = bench.measure_recovery()
    assert len(rows) == 8
    for row in rows:
        assert row["clue_recovered"], f'{row["case"]}: {row["recovery_status"]}'
        assert row["bytes_to_clue"] == row["visible_bytes"] + row["recovery_response_bytes"]
        assert row["store_bytes"] >= 0


def test_recovery_costs_one_call_and_lands_below_the_original():
    rows = {r["case"]: r for r in bench.measure_recovery()}
    buried = rows["8,000-line build log"]
    assert buried["recovery_calls"] == 1
    assert buried["clue_visible_in_compressed"] is False
    assert buried["bytes_to_clue"] < buried["original_bytes"]


def test_the_patch_clue_is_visible_without_recovery():
    rows = {r["case"]: r for r in bench.measure_recovery()}
    assert rows["600 KB patch"]["clue_visible_in_compressed"] is True
    assert rows["600 KB patch"]["recovery_calls"] == 0


def test_the_timing_policy_defers_instead_of_publishing_a_loaded_run():
    """One runnable task per CPU is the line; above it the harness defers."""
    quiet = {"load_1min": 0.4, "cpu_count": 4}
    busy = {"load_1min": 20.8, "cpu_count": 4}
    assert bench.load_policy(quiet)[0] is True
    run, reason = bench.load_policy(busy)
    assert run is False
    assert "per CPU" in reason
    assert bench.load_policy({"load_1min": None, "cpu_count": 4})[0] is False
    assert bench.load_policy({"load_1min": 0.1, "cpu_count": None})[0] is False


def test_environment_records_what_a_timing_needs_to_be_judged():
    info = bench.environment()
    for key in ("timestamp_utc", "python", "implementation", "platform",
                "cpu_count", "load_1min", "git_sha", "git_dirty"):
        assert key in info
    assert info["timestamp_utc"].endswith("+00:00")


def test_timed_keeps_every_trial_and_reports_the_spread():
    stats = bench.timed(lambda: None, warmups=1, repeats=4)
    assert len(stats["trials_ms"]) == 4
    assert stats["min_ms"] <= stats["median_ms"] <= stats["max_ms"]
    assert stats["median_ms"] >= 0


def test_memory_reports_the_peak_heap_not_the_live_total():
    """(current, peak) is easy to swap; that mistake under-reports by 1000x."""
    payload = next(c for c in bench.build_payloads()
                   if c["case"] == "3,000-frame traceback")
    memory = bench.measure_memory(payload["text"], payload["case"])
    assert memory["peak_heap_kib"] > 100, memory
    assert memory["input_bytes"] == len(payload["text"].encode("utf-8"))


def test_artifacts_are_consistent_and_tampering_is_detected(tmp_path):
    assert bench.main(["--timing", "off", "--out", str(tmp_path)]) == 0
    assert bench.verify_artifacts(tmp_path) == []

    svg_path = tmp_path / "savings.svg"
    original = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(original.replace("\u2192", "->"), encoding="utf-8")
    problems = bench.verify_artifacts(tmp_path)
    assert any("savings.svg" in problem for problem in problems), problems
    assert bench.main(["--check", "--out", str(tmp_path)]) == 1


def test_charts_are_well_formed_svg():
    rows = bench.measure()
    svg = bench.render_svg(rows)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<svg") == 1
    for row in rows:
        assert row["case"] in svg

    recovery = bench.measure_recovery()
    chart = bench.render_recovery_svg(recovery)
    assert chart.startswith("<svg")
    assert chart.rstrip().endswith("</svg>")
    assert chart.count("<svg") == 1
    for row in recovery:
        assert row["case"] in chart
    assert json.loads(json.dumps(recovery))  # JSON-serialisable artifacts
