#!/usr/bin/env python3
"""Compare the four unified modes on the committed fixture corpus.

Run from the repository root:

    python3 bench/modes.py --check     # verify the harness and fixtures only
    python3 bench/modes.py             # write bench/modes.json and print a table

The measurement is local and deterministic in its byte counts: the payloads are
committed fixtures, the compressor and the store are the real plugin, and no
network or model is involved. Wall-clock numbers are reported but are not
stable enough to rank modes; the decision trace, not the timer, explains a
choice. Byte reduction is *not* provider token savings: no tokenizer or price is
applied here, and the report says so.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import importlib.util
import json
import pathlib
import platform
import re
import statistics
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "benchmarks" / "fixtures"
OUTPUT = ROOT / "bench" / "modes.json"
MODES = ("off", "balanced", "aggressive", "beast")
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")
FIELD_RE = re.compile(r'field="([^"]+)"')

spec = importlib.util.spec_from_file_location(
    "sutra_modes_pkg", ROOT / "__init__.py",
    submodule_search_locations=[str(ROOT)])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_modes_pkg"] = sutra
spec.loader.exec_module(sutra)

config = sys.modules["sutra_modes_pkg.config"]
core = sys.modules["sutra_modes_pkg.core"]
recovery = sys.modules["sutra_modes_pkg.recovery"]
store_module = sys.modules["sutra_modes_pkg.store"]


def _synthetic() -> list[dict[str, str]]:
    """Large deterministic payloads, shaped like ordinary tool traffic.

    The committed fixtures are small enough to fit the default byte budget, so
    on their own they exercise identity paths but not head/tail elision. These
    payloads are generated in the script, contain no recorded run, and are
    labelled synthetic in the report.
    """
    prose_line = ("Please note that the worker is able to read the file in "
                  "order to check the queue at this point in time.   \n")
    prose = prose_line * 700
    trace = "".join(
        f"  File \"worker.py\", line {index}, in handle_{index % 40}   \n"
        f"    result = call_{index % 17}(payload, retry={index % 3})\n"
        for index in range(1200))
    log = "Traceback (most recent call last):\n" + trace + (
        "RuntimeError: queue drained before the writer acknowledged it\n")
    document = json.dumps({"exit_code": 1, "output": log,
                           "note": "please   review the failure in order to "
                                   "fix it\u00a0\u00a0"})
    patch_lines = ["diff --git a/worker.py b/worker.py",
                   "index 1111111..2222222 100644", "--- a/worker.py",
                   "+++ b/worker.py", "@@ -1,3 +1,3 @@"]
    for index in range(1500):
        patch_lines.append(f"-old call {index}   ")
        patch_lines.append(f"+new call {index}(retry=True)")
    return [
        {"label": "synthetic-prose", "path": "(generated)", "text": prose,
         "synthetic": True},
        {"label": "synthetic-log", "path": "(generated)", "text": log,
         "synthetic": True},
        {"label": "synthetic-json", "path": "(generated)", "text": document,
         "synthetic": True},
        {"label": "synthetic-patch", "path": "(generated)",
         "text": "\n".join(patch_lines) + "\n", "synthetic": True},
    ]


def workloads() -> list[dict[str, str]]:
    """The fixture slices and generated payloads this harness compares."""
    choices = (
        ("large-json", "general/large_json/large_json_api_response.txt"),
        ("stack-trace", "general/long_stack_trace/python_recursion_error.txt"),
        ("repeated-test-output",
         "general/repeated_test_output/repeated_pytest_output.txt"),
        ("git-diff", "general/git_diff/git_diff_unstaged.txt"),
        ("git-patch", "general/git_patch/git_patch_staged.patch"),
        ("compiler-errors", "general/compiler_errors/gcc_type_error.txt"),
    )
    picked: list[dict[str, str]] = []
    for label, relative in choices:
        path = FIXTURES / relative
        if path.is_file():
            picked.append({"label": label, "path": str(path),
                           "text": path.read_text(encoding="utf-8")})
    return picked + _synthetic()


def _cfg(mode: str, directory: pathlib.Path):
    base = config.resolve()
    return dataclasses.replace(
        base,
        enabled=True,
        mode=mode,
        mode_source="explicit",
        store_dir=str(directory),
        store_enabled=True,
        store_max_bytes=8 * 1024 * 1024,
        store_max_entry_bytes=4 * 1024 * 1024,
        store_max_entries=64,
        reuse=False,
    )


def _run_once(text: str, mode: str, directory: pathlib.Path,
              tool: str) -> dict[str, object]:
    cfg = _cfg(mode, directory)
    started = time.perf_counter()
    outcome = core.process_text(text, cfg=cfg, tool_name=tool,
                                session_id=f"bench-{mode}")
    elapsed = (time.perf_counter() - started) * 1000.0
    store = store_module.Store(store_module.StoreConfig.from_config(cfg))
    restored = None
    match = ID_RE.search(outcome.output)
    if match:
        request = {"id": match.group(1), "mode": "bytes", "start": 0,
                   "end": 512}
        field = FIELD_RE.search(outcome.output)
        if field:
            request["field"] = field.group(1)
        response = json.loads(recovery.restore(request, store=store))
        restored = response.get("status")
    original_bytes = len(text.encode("utf-8"))
    return {
        "mode": mode,
        "original_bytes": original_bytes,
        "output_bytes": len(outcome.output.encode("utf-8")),
        "byte_reduction_pct": (
            round((1 - len(outcome.output.encode("utf-8")) / original_bytes)
                  * 100, 3) if original_bytes else 0.0),
        "changed": outcome.changed,
        "marker": outcome.marker,
        "restore_status": restored,
        "elapsed_ms": round(elapsed, 3),
    }


def _trials(text: str, mode: str, tool: str, trials: int) -> dict[str, object]:
    runs = []
    with tempfile.TemporaryDirectory(prefix="sutra-modes-") as directory:
        for index in range(trials):
            target = pathlib.Path(directory) / f"trial-{index}"
            target.mkdir()
            runs.append(_run_once(text, mode, target, tool))
    representative = runs[0]
    representative["elapsed_ms_median"] = round(
        statistics.median(run["elapsed_ms"] for run in runs), 3)
    representative["elapsed_ms_trials"] = [run["elapsed_ms"] for run in runs]
    return representative


def _trace(text: str, mode: str, tool: str) -> dict[str, object]:
    orchestrator = importlib.import_module("sutra_modes_pkg.orchestrator")
    try:
        json.loads(text)
        structured = True
    except ValueError:
        structured = False
    patch = tool == "patch" or text.lstrip().startswith(
        ("diff --git", "--- a/", "Index: "))
    with tempfile.TemporaryDirectory(prefix="sutra-modes-trace-") as directory:
        cfg = _cfg(mode, pathlib.Path(directory))
        report = orchestrator.optimize_text(
            text, cfg=cfg, structured=structured, patch=patch)
    return {
        "chosen": report.chosen,
        "saved_bytes": report.saved_bytes,
        "plans_evaluated": report.decision.get("plans_evaluated", []),
        "reason": report.decision.get("reason", ""),
        "sace": report.decision.get("sace"),
        "pareto": report.decision.get("pareto", []),
    }


def measure(trials: int = 3) -> dict[str, object]:
    results = []
    for workload in workloads():
        raw_path = pathlib.Path(workload["path"])
        shown_path = (str(raw_path.relative_to(ROOT))
                      if raw_path.is_absolute() else workload["path"])
        entry: dict[str, object] = {"label": workload["label"],
                                    "path": shown_path,
                                    "modes": {}}
        tool = "terminal"
        for mode in MODES:
            entry["modes"][mode] = _trials(workload["text"], mode, tool, trials)
        entry["json_valid"] = []
        if workload["label"] == "large-json":
            for mode in MODES:
                cfg = _cfg(mode, pathlib.Path(tempfile.mkdtemp()))
                outcome = core.process_text(workload["text"], cfg=cfg,
                                            tool_name=tool,
                                            session_id=f"json-{mode}")
                try:
                    json.loads(outcome.output)
                    entry["json_valid"].append(mode)
                except ValueError:
                    pass
        if workload["label"] in ("git-patch", "synthetic-patch"):
            entry["patch_identical"] = all(
                _run_once(workload["text"], mode,
                          pathlib.Path(tempfile.mkdtemp()), tool)["output_bytes"]
                == len(workload["text"].encode("utf-8"))
                for mode in MODES)
        entry["synthetic"] = bool(workload.get("synthetic"))
        entry["trace"] = {mode: _trace(workload["text"], mode, tool)
                          for mode in MODES}
        results.append(entry)
    return {
        "version": getattr(sutra, "__version__", "unknown"),
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "git_sha": _git_sha(),
        },
        "method": (
            "core.process_text per mode with a fresh store; byte counts are "
            "exact; wall-clock is median of three trials on this host and is "
            "not used to rank modes; no provider tokens or prices are claimed"),
        "results": results,
    }


def _git_sha() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def _print_table(payload: dict[str, object]) -> None:
    print("| workload | mode | bytes | reduction | changed | restore | chosen |")
    print("| --- | --- | ---: | ---: | --- | --- | --- |")
    for entry in payload["results"]:  # type: ignore[index]
        label = entry["label"]
        for mode in MODES:
            row = entry["modes"][mode]
            trace = entry["trace"][mode]
            print(f"| {label} | {mode} | {row['output_bytes']} | "
                  f"{row['byte_reduction_pct']:.2f}% | {row['changed']} | "
                  f"{row['restore_status'] or '-'} | "
                  f"{trace['chosen'] or '-'} |")
    print("\nLocal byte measurements only; no provider token or cost claim.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify fixtures and imports without measuring")
    parser.add_argument("--trials", type=int, default=3,
                        help="timing trials per workload (default 3)")
    parser.add_argument("--out", default=str(OUTPUT),
                        help="where to write the raw JSON report")
    args = parser.parse_args(argv)
    if args.check:
        found = workloads()
        print(f"{len(found)} fixtures ready; modes={', '.join(MODES)}")
        if not found:
            print("no fixtures found", file=sys.stderr)
            return 1
        for entry in found:
            path = pathlib.Path(entry["path"])
            if not entry["text"]:
                print(f"empty payload: {path}", file=sys.stderr)
                return 1
            if not entry.get("synthetic") and not path.is_file():
                print(f"missing fixture: {path}", file=sys.stderr)
                return 1
        return 0
    payload = measure(max(1, args.trials))
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    _print_table(payload)
    print(f"\nraw report: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
