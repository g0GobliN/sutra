"""Tests for the Phase Four A/B runner extensions: expanded task corpus
(8 -> 30 representative repo tasks), paired experiment mode
(control off / treatment on, identical conditions), and the usage /
tool-call / SUTRA-evidence metrics, plus the report's statistical-honesty
and cost helpers.

Hermetic: no model, no `hermes` host — dry runs and scorer self-checks only.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load("sutra_ab_run", ROOT / "scripts" / "ab" / "run.py")
report = _load("sutra_ab_report", ROOT / "scripts" / "ab" / "report.py")

BUILTIN = ("log-failure", "traceback", "assertion", "patch", "json-record",
           "buried", "boundary", "coding")
EXTRA = ("eslint", "mypy", "pytest-fail", "cargo-fail", "go-fail",
         "docker-fail", "npm-conflict", "git-diff", "trace-deep",
         "passing-swamp", "config-hunt", "trace-id", "deprecation",
         "coverage", "flaky-test", "race-detector", "deadlock", "oom-killer",
         "slow-query", "py-fix-factorial", "py-fix-grades", "py-fix-pricing")
ALL_TASKS = BUILTIN + EXTRA


def test_task_corpus_expanded_to_thirty_representative_tasks():
    names = [name for name, _, _, _ in run.TASKS]
    assert len(run.TASKS) == 30, len(run.TASKS)
    assert len(names) == len(set(names)), "duplicate task names"
    assert list(names[:8]) == list(BUILTIN)
    assert list(names[8:]) == list(EXTRA)
    assert len(run.EXTRA_TASKS) == 22


def test_paired_flag_stored_true_defaults_false(monkeypatch, tmp_path):
    captured = {}

    def fake_execute(args):
        captured["paired"] = args.paired
        return 0

    monkeypatch.setattr(run, "execute", fake_execute)
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--paired", "--dry-run", "--out",
                         str(tmp_path / "r1")])
    assert run.main() == 0
    assert captured["paired"] is True

    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--dry-run", "--out",
                         str(tmp_path / "r2")])
    assert run.main() == 0
    assert captured["paired"] is False


def test_existing_arm_interface_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--selfcheck", "--arm", "bogus"])
    with pytest.raises(SystemExit) as excinfo:
        run.main()
    assert excinfo.value.code == 2


def test_scorer_selfcheck_passes_for_all_thirty_tasks():
    assert run.selfcheck() == 0


def test_help_survives_a_cp932_console():
    env = {**os.environ, "PYTHONIOENCODING": "cp932"}
    for name in ("run.py", "report.py"):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ab" / name), "--help"],
            capture_output=True, timeout=60, cwd=str(ROOT), env=env)
        stderr = completed.stderr.decode("cp932", "replace")
        assert completed.returncode == 0, (name, stderr)
        assert b"usage:" in completed.stdout, name


def test_dry_run_records_one_row_per_task_for_both_arms(tmp_path):
    out = tmp_path / "ab"
    for arm in ("off", "on"):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ab" / "run.py"),
             "--dry-run", "--paired", "--arm", arm, "--out", str(out)],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        assert completed.returncode == 0, completed.stderr

    def read(arm):
        path = out / f"results-{arm}-pass1.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip()]

    off, on = read("off"), read("on")
    assert len(off) == len(on) == 30
    for records in (off, on):
        assert {r["task"] for r in records} == set(ALL_TASKS)
        assert all(r["scored"] is False for r in records)  # mechanics, not results
        assert all(r["paired"] is True for r in records)
        assert all(r["dry_run"] is True for r in records)
        assert all(r["usage"] is None for r in records)
        assert all(r["tool_calls"] is None for r in records)
        assert all(r["runner_tokens"] is None for r in records)
        assert all(r["sutra_evidence"] is None for r in records)
        assert all(r["arm"] in ("off", "on") for r in records)


def test_ci_wilson_reports_sample_honest_intervals():
    assert report.ci_wilson(0, 0) is None  # no trials, no interval
    lo, hi = report.ci_wilson(0, 10)
    assert lo == 0.0 and 0.0 < hi < 1.0  # all failures still get an interval
    lo, hi = report.ci_wilson(10, 10)
    assert hi == 1.0 and 0.0 < lo < 1.0  # all passes stay bounded away from 1
    lo, hi = report.ci_wilson(3, 10)
    assert 0.0 < lo < 0.3 < hi < 1.0
    wide = report.ci_wilson(5, 10)[1] - report.ci_wilson(5, 10)[0]
    narrow = report.ci_wilson(50, 100)[1] - report.ci_wilson(50, 100)[0]
    assert wide > narrow  # repetitions tighten the interval


def test_cost_usd_never_fabricates_a_bill():
    assert report.cost_usd(None, None, None) is None
    assert report.cost_usd(None, None) == 0.0  # known model, missing counts -> zero, not a guess
    assert report.cost_usd(None, 100, "no-such-model") is None
    assert report.cost_usd(1_000_000, 1_000_000) == 12.5
    assert report.cost_usd(1_000_000, 0) == 2.5
    assert report.cost_usd(0, 1_000_000) == 10.0


def test_report_helpers_read_optional_fields_gracefully():
    full = {"sutra_evidence": {"restore_calls": 2, "restore_statuses": ["ok"]},
            "runner_tokens": {"input": 100, "output": 50}}
    assert report.recovery_calls(full) == 2
    assert report.recovery_calls({}) == 0
    assert report.recovery_calls({"sutra_evidence": None}) == 0
    assert report.runner_tokens(full) == (100, 50)
    assert report.runner_tokens({}) == (None, None)
    assert report.fmt_usd(None) == "unreported"
    assert report.fmt_usd(0.0012345) == "0.0012"


def test_report_renders_ci_cost_statistical_honesty_and_deviations(tmp_path):
    off_record = {
        "arm": "off", "task": "log-failure", "seed": 1, "pass_index": 1,
        "scored": True, "timed_out": False, "duration_s": 1.5,
        "usage": {"total": {"input_tokens": 1000, "output_tokens": 500,
                            "actual_cost_usd": 0.0075}},
        "runner_tokens": {"input": 900, "output": 450},
        "tool_calls": 4, "sutra_evidence": None,
        "exit_code": 0, "stderr_tail": "",
    }
    on_record = {
        "arm": "on", "task": "log-failure", "seed": 1, "pass_index": 1,
        "scored": False, "timed_out": True, "duration_s": 900.0,
        "usage": None,
        "runner_tokens": {"input": 900, "output": 450},
        "tool_calls": 0,
        "sutra_evidence": {"tool_results_with_marker": 2, "restore_calls": 1,
                           "restore_statuses": ["ok"]},
        "exit_code": None, "stderr_tail": "timed out after 900s",
    }
    p_off = tmp_path / "results-off-pass1.jsonl"
    p_on = tmp_path / "results-on-pass1.jsonl"
    p_off.write_text(json.dumps(off_record) + "\n", encoding="utf-8")
    p_on.write_text(json.dumps(on_record) + "\n", encoding="utf-8")
    out = tmp_path / "report.md"
    assert report.main([str(p_off), str(p_on), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| tasks | 1 |" in text
    assert "| trials per arm per task | 1–1 recorded sessions |" in text
    assert "| total sessions | 2 |" in text
    assert "Wilson score, 95% (z = 1.96)" in text
    assert "1/1 tasks." in text  # regression rate: treatment fail, control pass
    assert "unreported" in text  # provider usage not reported for the on arm
    assert "- on/log-failure (seed 1): timed out" in text  # deviation listed, never dropped
    assert "Deterministic compression savings" in text
    assert "documented default rates for gpt-4o; estimates only, not provider bills" in text
