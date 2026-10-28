"""The A/B protocol's scorers are frozen before the first live run — CI holds them.

docs/ab-benchmark.md is the contract; scripts/ab/run.py implements it. These
tests keep the two in sync without any model: the scorer self-check must pass
(positive and negative), and every text fixture must be sized for the real
pipeline — big enough that SUTRA rewrites it (over the 24,576 B threshold),
small enough to survive Hermes' own 50,000-byte terminal cap.
"""

import argparse
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
def test_a_live_run_aborts_when_the_host_state_cannot_be_read(tmp_path, monkeypatch, capsys):
    """Unknown state is not a guess: the run stops before any fixture work, with a
    message that names the probe rather than claiming a plugin mismatch."""
    bin_dir = _hanging_hermes(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(ab, "HOST_PROBE_TIMEOUT_S", 1.0)
    args = argparse.Namespace(arm="on", dry_run=False, out=str(tmp_path / "v5"),
                              pass_index=1, seed=1, timeout=1)
    assert ab.execute(args) == 2
    out = capsys.readouterr().out
    assert "could not read the live plugin state" in out
    assert not (tmp_path / "v5" / "results-on-pass1.jsonl").exists()
