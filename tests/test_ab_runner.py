"""Tests for the A/B protocols: the original frozen eight-task v1 corpus,
the expanded 30-task v2 corpus, paired experiment mode
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


def _report_module():
    return report

BUILTIN = ("log-failure", "traceback", "assertion", "patch", "json-record",
           "buried", "boundary", "coding")
EXTRA = ("eslint", "mypy", "pytest-fail", "cargo-fail", "go-fail",
         "docker-fail", "npm-conflict", "git-diff", "trace-deep",
         "passing-swamp", "config-hunt", "trace-id", "deprecation",
         "coverage", "flaky-test", "race-detector", "deadlock", "oom-killer",
         "slow-query", "py-fix-factorial", "py-fix-grades", "py-fix-pricing")
ALL_TASKS = BUILTIN + EXTRA


def test_protocol_v1_is_the_original_eight_ordered_tasks():
    names = [name for name, _, _, _ in run.PROTOCOLS["v1"]]
    assert names == list(BUILTIN)
    assert len(names) == len(set(names)), "duplicate task names"


def test_protocol_v2_is_the_expanded_thirty_task_corpus():
    names = [name for name, _, _, _ in run.PROTOCOLS["v2"]]
    assert len(names) == 30, len(names)
    assert len(names) == len(set(names)), "duplicate task names"
    assert tuple(names) == ALL_TASKS
    assert len(run.EXTRA_TASKS) == 22


def test_default_protocol_is_v1(monkeypatch, tmp_path):
    captured = {}

    def fake_execute(args):
        captured["protocol"] = args.protocol
        return 0

    monkeypatch.setattr(run, "execute", fake_execute)
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--dry-run", "--out", str(tmp_path / "r")])
    assert run.main() == 0
    assert run.DEFAULT_PROTOCOL == "v1"
    assert captured["protocol"] == "v1"


def test_paired_flag_stored_true_defaults_false(monkeypatch, tmp_path):
    captured = {}

    def fake_execute(args):
        captured["paired"] = args.paired
        captured["protocol"] = args.protocol
        return 0

    monkeypatch.setattr(run, "execute", fake_execute)
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--paired", "--protocol", "v2", "--dry-run", "--out",
                         str(tmp_path / "r1")])
    assert run.main() == 0
    assert captured["paired"] is True
    assert captured["protocol"] == "v2"

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


def test_scorer_selfcheck_passes_for_both_protocols():
    assert run.selfcheck() == 0
    assert run.selfcheck(["v1"]) == 0
    assert run.selfcheck(["v2"]) == 0


def test_help_survives_a_cp932_console():
    env = {**os.environ, "PYTHONIOENCODING": "cp932"}
    for name in ("run.py", "report.py"):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ab" / name), "--help"],
            capture_output=True, timeout=60, cwd=str(ROOT), env=env)
        stderr = completed.stderr.decode("cp932", "replace")
        assert completed.returncode == 0, (name, stderr)
        assert b"usage:" in completed.stdout, name


@pytest.mark.parametrize(
    ("protocol", "expected_tasks"),
    (("v1", BUILTIN), ("v2", ALL_TASKS)),
)
def test_dry_run_records_one_row_per_task_for_both_arms(
        tmp_path, protocol, expected_tasks):
    out = tmp_path / "ab"
    for arm in ("off", "on"):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ab" / "run.py"),
             "--dry-run", "--paired", "--protocol", protocol,
             "--arm", arm, "--out", str(out)],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        assert completed.returncode == 0, completed.stderr

    def read(arm):
        path = out / f"results-{protocol}-{arm}-pass1.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip()]

    off, on = read("off"), read("on")
    assert len(off) == len(on) == len(expected_tasks)
    for records in (off, on):
        assert {r["task"] for r in records} == set(expected_tasks)
        assert all(r["protocol"] == protocol for r in records)
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
        "protocol": "v1", "arm": "off", "task": "log-failure", "seed": 1,
        "pass_index": 1,
        "scored": True, "timed_out": False, "duration_s": 1.5,
        "usage": {"total": {"input_tokens": 1000, "output_tokens": 500,
                            "cache_read_tokens": 4000,
                            "cache_write_tokens": 250,
                            "total_prompt_tokens": 5250,
                            "actual_cost_usd": 0.0075}},
        "runner_tokens": {"input": 900, "output": 450},
        "tool_calls": 4, "sutra_evidence": None,
        "exit_code": 0, "stderr_tail": "",
    }
    on_record = {
        "protocol": "v1", "arm": "on", "task": "log-failure", "seed": 1,
        "pass_index": 1,
        "scored": False, "timed_out": True, "duration_s": 900.0,
        "usage": None,
        "runner_tokens": {"input": 900, "output": 450},
        "tool_calls": 0,
        "sutra_evidence": {"tool_results_with_marker": 2, "restore_calls": 1,
                           "restore_statuses": ["ok"]},
        "exit_code": None, "stderr_tail": "timed out after 900s",
    }
    p_off = tmp_path / "results-v1-off-pass1.jsonl"
    p_on = tmp_path / "results-v1-on-pass1.jsonl"
    p_off.write_text(json.dumps(off_record) + "\n", encoding="utf-8")
    p_on.write_text(json.dumps(on_record) + "\n", encoding="utf-8")
    out = tmp_path / "report.md"
    assert report.main([str(p_off), str(p_on), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "- protocol: v1" in text
    assert "| tasks | 1 |" in text
    assert "| trials per arm per task | 1–1 recorded sessions |" in text
    assert "| total sessions | 2 |" in text
    assert "Wilson score, 95% (z = 1.96)" in text
    assert "1/1 tasks." in text  # regression rate: treatment fail, control pass
    assert "unreported" in text  # provider usage not reported for the on arm
    assert "- on/log-failure (seed 1): timed out" in text  # deviation listed, never dropped
    assert "Deterministic compression savings" in text
    assert "documented default rates for gpt-4o; estimates only, not provider bills" in text
    # The uncached remainder is not the prompt: the table states both.
    assert "| in tok (uncached) |" in text
    assert "| cache read | cache write | prompt tok (total) |" in text
    assert "| 1,000 | 500 | 4,000 | 250 | 5,250 |" in text
    assert "Comparing the uncached column alone can make a" in text


def test_prompt_tokens_sums_the_three_buckets():
    """A prompt is uncached + cache read + cache write; the stated total wins."""
    report = _report_module()
    assert report.prompt_tokens(None) is None
    assert report.prompt_tokens({"input_tokens": 10}) == 10
    assert report.prompt_tokens(
        {"input_tokens": 10, "cache_read_tokens": 90,
         "cache_write_tokens": 5}) == 105
    assert report.prompt_tokens(
        {"input_tokens": 10, "cache_read_tokens": 90,
         "total_prompt_tokens": 100}) == 100


def test_a_cached_prompt_is_not_a_small_prompt(tmp_path):
    """Two arms can differ 38% in uncached tokens while the prompt is the same
    size; the report must show the total so that difference is visible."""
    report = _report_module()
    common = {"protocol": "v2", "task": "json-record", "seed": 1,
              "pass_index": 1, "scored": True, "timed_out": False,
              "duration_s": 40.0, "runner_tokens": None, "tool_calls": 4,
              "sutra_evidence": None, "exit_code": 0, "stderr_tail": ""}
    on = {**common, "arm": "on", "usage": {"total": {
        "input_tokens": 18_129, "output_tokens": 389,
        "cache_read_tokens": 98_176, "cache_write_tokens": 0,
        "total_prompt_tokens": 116_305}}}
    off = {**common, "arm": "off", "usage": {"total": {
        "input_tokens": 29_515, "output_tokens": 721,
        "cache_read_tokens": 85_376, "cache_write_tokens": 0,
        "total_prompt_tokens": 114_891}}}
    p_on = tmp_path / "results-v2-on-pass1.jsonl"
    p_off = tmp_path / "results-v2-off-pass1.jsonl"
    p_on.write_text(json.dumps(on) + "\n", encoding="utf-8")
    p_off.write_text(json.dumps(off) + "\n", encoding="utf-8")
    out = tmp_path / "report.md"
    assert report.main([str(p_off), str(p_on), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| 18,129 | 389 | 98,176 | 0 | 116,305 |" in text
    assert "| 29,515 | 721 | 85,376 | 0 | 114,891 |" in text


def _write_record(path, protocol, arm, task):
    record = {"protocol": protocol, "arm": arm, "task": task, "seed": 1,
              "pass_index": 1, "scored": True, "timed_out": False,
              "duration_s": 1.0, "usage": None, "runner_tokens": None,
              "tool_calls": 0, "sutra_evidence": None, "exit_code": 0,
              "stderr_tail": ""}
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_report_rejects_mixed_protocol_evidence(tmp_path, capsys):
    p_v1 = tmp_path / "results-v1-off-pass1.jsonl"
    p_v2 = tmp_path / "results-v2-on-pass1.jsonl"
    _write_record(p_v1, "v1", "off", "log-failure")
    _write_record(p_v2, "v2", "on", "log-failure")
    out = tmp_path / "report.md"
    assert report.main([str(p_v1), str(p_v2), "--out", str(out)]) == 1
    assert "mixed protocols" in capsys.readouterr().out
    assert not out.exists()


def test_report_rejects_records_without_a_protocol(tmp_path, capsys):
    path = tmp_path / "results-on-pass1.jsonl"
    path.write_text(json.dumps({"arm": "on", "task": "log-failure"}) + "\n",
                    encoding="utf-8")
    assert report.main([str(path)]) == 1
    assert "do not identify a protocol" in capsys.readouterr().out


def test_a_session_id_is_read_from_either_stream():
    """`hermes chat -q` prints `session_id:` on stderr; the interactive form
    prints `Session:` on stdout. A stderr-only run used to record None, which
    silently dropped the usage row from every result."""
    module = run
    stdout = "the answer\n"
    stderr = "\nsession_id: local-abc123\n"
    match = module.SESSION_RE.search(stdout + "\n" + stderr)
    assert match and match.group(1) == "local-abc123"
    assert module.SESSION_RE.search("Session: local-def456\n").group(1) == "local-def456"


def test_a_request_rewrite_is_attributed_to_its_session(tmp_path, monkeypatch):
    stats = tmp_path / "sutra-stats.jsonl"
    monkeypatch.setenv("SUTRA_STATS_FILE", str(stats))
    log = stats.with_name(stats.stem + "-request.jsonl")
    log.write_text(
        '{"at": "2026-11-22T00:00:00Z", "mode": "aggressive", "session": "local-a",'
        ' "before": 1000, "after": 400, "saved": 600}\n'
        '{"at": "2026-11-22T00:00:01Z", "mode": "beast", "session": "local-b",'
        ' "before": 900, "after": 300, "saved": 600}\n',
        encoding="utf-8")
    evidence = run.session_request_evidence("local-a")
    assert evidence["request_rewrites"] == 1
    assert evidence["request_saved_bytes"] == 600
    assert evidence["request_modes"] == ["aggressive"]
    assert run.session_request_evidence("nobody")["request_rewrites"] == 0
