"""Tests for the Phase 1 eval harness (eval/run_harness.py)."""

from __future__ import annotations

import json
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "eval") not in sys.path:
    sys.path.insert(0, str(ROOT / "eval"))

import compare  # noqa: E402
import run_harness  # noqa: E402


class _LegacyConsole(io.StringIO):
    """A console like Windows' cp932: it cannot encode the report's em dash."""

    encoding = "cp932"


def test_run_report_never_raises_on_a_legacy_console(monkeypatch):
    console = _LegacyConsole()
    monkeypatch.setattr(sys, "stdout", console)

    run_harness.print_run_report({
        "run_id": "run-test", "sutra_version": "0.0.0", "reps": 1,
        "conditions": {"A": {"label": "control"}},
        "tasks": [], "passed": True, "passed_reps": 0, "total_reps": 0,
    })

    rendered = console.getvalue()
    assert "SUTRA eval" in rendered
    assert "\u2014" not in rendered
    rendered.encode("cp932")

SUTRA_ENV = (
    "SUTRA_ENABLED", "SUTRA_MAX_BYTES", "SUTRA_HEAD_BYTES", "SUTRA_TAIL_BYTES",
    "SUTRA_STORE", "SUTRA_STORE_DIR", "SUTRA_POLICY", "SUTRA_REUSE",
    "SUTRA_REUSE_MIN_BYTES", "SUTRA_RECOVERY_MAX_BYTES",
    "SUTRA_RECOVERY_MAX_MATCHES", "HERMES_HOME",
)

EVIDENCE_TAGS = {
    "IMPLEMENTED", "TESTED", "EXPERIMENTALLY DEMONSTRATED", "PRODUCTION VERIFIED",
}


@pytest.fixture(autouse=True)
def isolated_sutra_env():
    """Each test starts with a clean SUTRA environment; restore afterwards."""
    saved = {name: os.environ.get(name) for name in SUTRA_ENV}
    for name in SUTRA_ENV:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_condition_env_contract():
    expected = {
        "A": {"SUTRA_ENABLED": "0", "SUTRA_STORE": "0", "SUTRA_POLICY": "off", "SUTRA_REUSE": "0"},
        "B": {"SUTRA_ENABLED": "1", "SUTRA_STORE": "1", "SUTRA_POLICY": "off", "SUTRA_REUSE": "0"},
        "C": {"SUTRA_ENABLED": "1", "SUTRA_STORE": "1", "SUTRA_POLICY": "balanced", "SUTRA_REUSE": "1"},
    }
    for condition, want in expected.items():
        env = run_harness.CONDITIONS[condition]["env"]
        for key, value in want.items():
            assert env[key] == value, f"{condition}.{key}"


def test_compute_diffs_basic():
    summary = {
        "run_id": "run-test",
        "tasks": [
            {
                "task_id": "eval-x",
                "task_type": "standard",
                "difficulty_rating": "easy",
                "evidence_level": "TESTED",
                "evidence_achieved": "TESTED",
                "conditions": {
                    "A": {"aggregate": {
                        "visible_bytes": {"mean": 1000.0}, "compression_ms": {"mean": 0.0},
                        "clues_lost": 0, "exact_failures": 0,
                        "protected_ok": 4, "reuse_hits": 0,
                        "pass_rate": {"rate": 1.0}}},
                    "B": {"aggregate": {
                        "visible_bytes": {"mean": 600.0}, "compression_ms": {"mean": 5.0},
                        "clues_lost": 0, "exact_failures": 0,
                        "protected_ok": 4, "reuse_hits": 0,
                        "pass_rate": {"rate": 1.0}}},
                    "C": {"aggregate": {
                        "visible_bytes": {"mean": 600.0}, "compression_ms": {"mean": 7.0},
                        "clues_lost": 0, "exact_failures": 0,
                        "protected_ok": 4, "reuse_hits": 2,
                        "pass_rate": {"rate": 1.0}}},
                },
            }
        ],
    }
    comparison = compare.compute_comparison(summary)
    metrics = {}
    for pair in comparison["tasks"][0]["pairs"]:
        if pair["pair"] == "B_vs_A":
            metrics = {m["metric"]: m for m in pair["metrics"]}
    assert metrics["visible_bytes"]["diff_abs"] == -400.0
    assert metrics["visible_bytes"]["diff_pct"] == -40.0
    assert metrics["visible_bytes"]["direction"] == "improved"

    reuse = None
    for pair in comparison["tasks"][0]["pairs"]:
        if pair["pair"] == "C_vs_B":
            reuse = {m["metric"]: m for m in pair["metrics"]}
    assert reuse["reuse_hits"]["diff_abs"] == 2.0
    assert reuse["reuse_hits"]["diff_pct"] is None  # baseline of 0 -> no pct


def test_format_comparison_runs():
    comparison = {
        "run_id": "run-test",
        "pairs": ["B_vs_A", "C_vs_A", "C_vs_B"],
        "tasks": [],
        "overall": {"pairs": []},
        "next_steps": ["all conditions stable"],
    }
    text = compare.format_comparison(comparison, tty=False)
    assert "run-test" in text
    assert "next steps" in text


def test_all_tasks_load_and_are_runnable():
    tasks = run_harness.load_tasks()
    assert [t["task_id"] for t in tasks] == [f"eval-{i:03d}" for i in range(1, 9)]
    for task in tasks:
        assert task["runnable"], task.get("error")
        assert callable(task["scenario"])
        assert task["task_id"] and task["description"]
        assert task["evidence_level"] in EVIDENCE_TAGS
        assert task["difficulty_rating"] in ("Trivial", "Easy", "Moderate", "Hard", "Complex")


def test_run_single_task_all_conditions(tmp_path):
    summary, comparison, run_dir = run_harness.run_all(
        task_ids=["eval-008"], conditions=["A", "B", "C"], reps=2,
        out_dir=str(tmp_path / "results"))
    assert summary["passed"] is True
    task = summary["tasks"][0]
    assert set(task["conditions"]) == {"A", "B", "C"}
    assert task["evidence_achieved"] == "PRODUCTION VERIFIED"
    for condition in "ABC":
        cond = task["conditions"][condition]
        assert len(cond["reps"]) == 2
        assert all(r["passed"] for r in cond["reps"])
        assert cond["aggregate"]["pass_rate"]["rate"] == 1.0
    assert (run_dir / "raw-eval-008-A.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "comparison.json").exists()


def test_run_eval_001_byte_exact_recovery(tmp_path):
    summary, _, _ = run_harness.run_all(
        task_ids=["eval-001"], conditions=["A", "B", "C"], reps=2,
        out_dir=str(tmp_path / "results"))
    assert summary["passed"] is True
    task = summary["tasks"][0]
    for rep in task["conditions"]["B"]["reps"]:
        step2 = rep["steps"][1]  # 24_577-byte payload, over budget by one
        assert step2["changed"] is True
        assert step2["id"] is not None
        assert step2["restore_ok"] is True, rep["steps"]
    for rep in task["conditions"]["C"]["reps"]:
        step2 = rep["steps"][1]
        assert step2["restore_ok"] is True


def test_unknown_task_id_fails(tmp_path):
    with pytest.raises(ValueError):
        run_harness.run_all(task_ids=["eval-999"], out_dir=str(tmp_path / "results"))


def test_reps_are_clamped_by_cli(tmp_path):
    from run_harness import main as harness_main
    out = str(tmp_path / "results")
    code = harness_main(["run", "--task", "eval-008", "--reps", "9", "--out", out])
    assert code == 0
    runs = sorted(Path(out).glob("run-*"))
    assert runs, "a run directory should have been created"
    summary = json.loads((runs[-1] / "summary.json").read_text())
    assert summary["reps"] == 5  # clamped to MAX_REPS


def test_cli_compare_with_no_run(tmp_path):
    from run_harness import main as harness_main
    code = harness_main(["compare", "--out", str(tmp_path / "results")])
    assert code == 2
