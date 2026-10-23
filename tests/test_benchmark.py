"""The benchmark's numbers are a claim in the README, so CI checks the claim."""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location("tooltrim_bench", os.path.join(ROOT, "bench", "benchmark.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_every_case_is_measured_and_never_grows():
    rows = bench.measure()
    assert len(rows) == 6
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


def test_chart_is_well_formed_svg():
    svg = bench.render_svg(bench.measure())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<svg") == 1
    for row in bench.measure():
        assert row["case"] in svg
