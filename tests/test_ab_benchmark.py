"""The A/B protocol's scorers are frozen before the first live run — CI holds them.

docs/ab-benchmark.md is the contract; scripts/ab/run.py implements it. These
tests keep the two in sync without any model: the scorer self-check must pass
(positive and negative), and every text fixture must be sized for the real
pipeline — big enough that SUTRA rewrites it (over the 24,576 B threshold),
small enough to survive Hermes' own 50,000-byte terminal cap.
"""

import importlib.util
import os
import pathlib
import random
import stat
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sutra_ab", ROOT / "scripts" / "ab" / "run.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def test_every_scorer_accepts_its_own_answer_and_rejects_a_wrong_one():
    assert ab.selfcheck() == 0


def test_every_text_fixture_is_sized_for_the_real_pipeline():
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="sutra-ab-size-"))
    for index, (name, _, setup, _) in enumerate(ab.TASKS):
        evidence = setup(workspace / name, random.Random(1_000 + index))
        if "test_cmd" in evidence:  # the coding task is a repo, not a payload
            continue
        size = ab.fixture_bytes(workspace / name, name)
        assert ab.MIN_PAYLOAD_BYTES <= size <= ab.MAX_PAYLOAD_BYTES, (name, size)


def test_buried_clue_is_elided_and_boundary_token_survives(sutra):
    """The two placement-sensitive tasks must hold against the real compressor:
    task 6's clue must vanish from the compressed copy (recovery is required),
    task 7's token must stay visible (boundary evidence is kept)."""
    for name, index, expected_visible in (("buried", 5, False), ("boundary", 6, True)):
        task, prompt, setup, scorer = ab.TASKS[index]
        del task, prompt, scorer
        workspace = pathlib.Path(tempfile.mkdtemp(prefix="sutra-ab-place-"))
        evidence = setup(workspace, random.Random(2_000 + index))
        payload_file = next(p for p in workspace.rglob("*")
                            if p.is_file() and p.stat().st_size > 1_000)
        out, stats = sutra.compress(payload_file.read_text(encoding="utf-8"))
        assert stats["rule"] == "head-tail", (name, stats["rule"])
        assert (evidence["planted"] in out) is expected_visible, name


def _hanging_hermes(tmp_path):
    """A `hermes` on PATH that never answers, to prove the runner never waits on it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "hermes"
    stub.write_text("#!/bin/sh\nexec sleep 300\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir


needs_posix_stub = pytest.mark.skipif(
    os.name == "nt", reason="a POSIX shell stub is needed to fake a hanging host")


def test_rerunning_into_existing_records_aborts(tmp_path):
    """A stale results file must never mix with a fresh run: the second
    invocation into the same arm/pass file aborts with a clear error."""
    import subprocess
    import sys as _sys
    out = tmp_path / "v5"
    first = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "ab" / "run.py"),
         "--dry-run", "--arm", "on", "--out", str(out)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert first.returncode == 0
    assert (out / "results-on-pass1.jsonl").stat().st_size > 0
    second = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "ab" / "run.py"),
         "--dry-run", "--arm", "on", "--out", str(out)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert second.returncode == 2
    assert "ABORT" in second.stdout and "mix" in second.stdout


@needs_posix_stub
def test_a_dry_run_never_probes_the_host(tmp_path):
    """A dry run exercises the mechanics only. It must not call `hermes`, which
    is a full CLI start (26.6 s on the box where this was found): with a host
    that never answers, the run still finishes promptly and writes its records."""
    bin_dir = _hanging_hermes(tmp_path)
    out = tmp_path / "v5"
    env = dict(os.environ,
               PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ab" / "run.py"),
         "--dry-run", "--arm", "on", "--out", str(out)],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT), env=env)
    elapsed = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    assert elapsed < 30, f"the dry run waited {elapsed:.0f}s on the host"
    assert "host state not probed" in done.stdout
    assert (out / "results-on-pass1.jsonl").stat().st_size > 0


@needs_posix_stub
def test_the_host_probe_is_bounded(tmp_path, monkeypatch):
    """The live probe keeps its own short budget: an unresponsive `hermes` becomes
    an unknown state, not a benchmark that hangs."""
    bin_dir = _hanging_hermes(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    started = time.monotonic()
    state = ab.plugin_state(timeout=1.0)
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"the probe took {elapsed:.1f}s"
    assert state == {"sutra_installed": None, "sutra_enabled": None}


@needs_posix_stub
def test_probe_timeout_flag_is_parsed_and_forwarded(tmp_path, monkeypatch, capsys):
    """`--probe-timeout` must reach the host probe through the real parser: with a
    `hermes` that never answers, 0.5 s aborts the whole run in well under the old
    120 s default. Unknown state is not a guess, so the run stops before any
    fixture work with a message that names the probe, not a plugin mismatch."""
    bin_dir = _hanging_hermes(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(ab, "HOST_PROBE_TIMEOUT_S", 1.0)
    seen = []
    real_probe = ab.plugin_state

    def capture(timeout=ab.HOST_PROBE_TIMEOUT_S):
        seen.append(timeout)
        return real_probe(timeout)

    monkeypatch.setattr(ab, "plugin_state", capture)
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--arm", "on", "--out", str(tmp_path / "v5"),
        "--pass-index", "1", "--seed", "1", "--timeout", "1",
        "--probe-timeout", "0.5",
    ])
    started = time.monotonic()
    assert ab.main() == 2
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"the configured probe took {elapsed:.1f}s"
    assert seen == [0.5]
    out = capsys.readouterr().out
    assert "could not read the live plugin state" in out
    assert not (tmp_path / "v5" / "results-on-pass1.jsonl").exists()


def test_probe_timeout_rejects_nonpositive_values(tmp_path, monkeypatch, capsys):
    """A zero or negative budget would make the probe useless; argparse rejects
    both before the runner can read any host state or write any record."""
    for value in ("0", "-1"):
        monkeypatch.setattr(sys, "argv", [
            "run.py", "--dry-run", "--out", str(tmp_path / "v5"),
            "--probe-timeout", value,
        ])
        with pytest.raises(SystemExit) as excinfo:
            ab.main()
        assert excinfo.value.code == 2
    assert capsys.readouterr().err.count("must be greater than zero") == 2
    assert not (tmp_path / "v5").exists()
