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
