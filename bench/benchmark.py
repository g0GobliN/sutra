#!/usr/bin/env python3
"""Measure SUTRA on representative tool output and draw the result.

Run from the repository root:

    python3 bench/benchmark.py                 # timings only on an idle machine
    python3 bench/benchmark.py --timing force  # record a contended run anyway
    python3 bench/benchmark.py --check         # re-verify existing artifacts

Writes ``bench/results.json``, ``bench/recovery.json``, ``bench/timing.json``,
``bench/savings.svg`` and ``bench/recovery.svg``, and prints the same numbers
as markdown. The byte and recovery measurements are deterministic: the payloads
are generated, the compressor and the store are the real plugin, and nothing
touches the network.

Timing has a load policy. A representative number needs a machine with roughly
one runnable task per CPU; above that the harness writes ``"status":
"deferred"`` with the measured load and no timings, rather than publishing a
contended figure. Every timing it does record keeps the raw trials, the
median, the min/max spread, and the environment (Python, platform, CPUs, RAM,
load, git SHA, timestamp) so the number can be judged.

What is *not* measured here: model input tokens, provider cost and task
success — those need a live model and a task harness; see docs/benchmark.md.
No token number in this repository is derived from a byte count.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "sutra_bench_pkg", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_bench_pkg"] = sutra
spec.loader.exec_module(sutra)

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")

TIMING_REPEATS = 5
TIMING_WARMUPS = 1
# One runnable task per CPU is already contention: on the development host a
# 20 ms measurement moved to 36 ms at load 19 on 4 CPUs, with individual trials
# spanning 76-147 ms. A timing run above this line is recorded, never published
# as representative.
LOAD_PER_CPU_LIMIT = 1.0


def build_payloads() -> "list[dict]":
    """One dict per measured case: label, note, text, clue, recovery mode."""
    lines = "\n".join(str(i) for i in range(1, 12_001))

    json_envelope = json.dumps(
        {"output": "\n".join(f"line {i} of a long command" for i in range(1, 3_001)),
         "exit_code": 0})

    test_run = "\n".join(
        ("FAIL src/module-1000.test.ts > case 1000 failed" if i == 1_000 else
         "\u2713 src/lib/module-{i}.test.ts > case {i} passes".format(i=i))
        for i in range(2_000))

    traceback_body = "\n".join(
        f'  File "/app/src/module_{i}.py", line {i}, in handler_{i}' for i in range(3_000))
    traceback = traceback_body + "\nTraceback (most recent call last):\n" + \
        traceback_body + "\nValueError: the thing broke\n"

    patch = "diff --git a/src/big.ts b/src/big.ts\n--- a/src/big.ts\n+++ b/src/big.ts\n@@ -1,2 +1,2 @@\n" + (
        "+const line = 'added by the patch'\n-const line = 'removed by the patch'\n" * 6_000)

    sql = "id\temail\tcreated_at\tstatus\n" + "\n".join(
        f"{i}\tuser{i}@example.com\t2026-10-{i % 28 + 1:02d}T09:00:00Z\tactive"
        for i in range(1, 901))

    build_log = "\n".join(
        ("src/parser.c:412:5: error: 'node' undeclared (first use in this function)"
         if i == 4_000 else f"[{i:05d}/08000] Compiling src/module_{i}.c")
        for i in range(8_000))

    json_array = json.dumps([{"id": i, "tag": "x"} for i in range(8_000)])

    return [
        {"case": "12,000-line command output", "note": "plain text, head + tail",
         "text": lines, "clue": "6001"},
        {"case": "JSON tool envelope", "note": "decoded, string fields trimmed",
         "text": json_envelope, "clue": "line 1500 of a long command"},
        {"case": "test run, 2,000 cases", "note": "repeat collapse + head/tail",
         "text": test_run, "clue": "FAIL src/module-1000.test.ts"},
        {"case": "3,000-frame traceback", "note": "error end preserved",
         "text": traceback, "clue": "ValueError: the thing broke"},
        {"case": "600 KB patch", "note": "never rewritten (safety case)",
         "text": patch, "clue": "+const line = 'added by the patch'"},
        {"case": "900-row SQL result", "note": "head + tail",
         "text": sql, "clue": "user450@example.com"},
        {"case": "8,000-line build log", "note": "one error buried in the middle",
         "text": build_log, "clue": "'node' undeclared"},
        {"case": "8,000-item JSON array", "note": "array stays an array (shape kept)",
         "text": json_array, "clue": '"id": 4000'},
    ]


def measure() -> "list[dict]":
    """Compression only: bytes in, bytes out, rule."""
    rows = []
    for case in build_payloads():
        text = case["text"]
        out, stats = sutra.compress(text)
        before = len(text.encode("utf-8"))
        after = len(out.encode("utf-8"))
        rows.append({
            "case": case["case"],
            "note": case["note"],
            "rule": stats["rule"],
            "before_bytes": before,
            "after_bytes": after,
            "saved_bytes": before - after,
            "saved_pct": round(100 * (before - after) / before, 1) if before else 0.0,
        })
    return rows


def measure_recovery() -> "list[dict]":
    """Compression + the real store + the real retrieval tool."""
    previous_home = os.environ.get("HERMES_HOME")
    home = tempfile.mkdtemp(prefix="sutra-bench-")
    os.environ["HERMES_HOME"] = home
    try:
        rows = []
        for case in build_payloads():
            text = case["text"]
            clue = case["clue"]
            result = sutra.compress_result({"output": text}, tool_name="terminal")
            visible = result["output"]
            visible_bytes = len(visible.encode("utf-8"))
            entry = ID_RE.search(visible)
            stored = entry is not None
            visible_has_clue = bool(clue) and clue in visible
            response_bytes = content_bytes = 0
            calls = 0
            recovered = visible_has_clue or clue is None
            status = "not_needed"
            if stored and clue and not visible_has_clue:
                calls = 1
                raw = sutra.recovery.restore(
                    {"id": entry.group(1), "mode": "search", "query": clue,
                     "max_matches": 1, "context": 1})
                payload = json.loads(raw)
                status = payload.get("status", "error")
                response_bytes = len(raw.encode("utf-8"))
                content_bytes = len(payload.get("content", "").encode("utf-8"))
                recovered = clue in payload.get("content", "")
            elif stored and clue:
                status = "visible"
            store_dir = pathlib.Path(home) / "cache" / "sutra-store"
            store_bytes = sum(p.stat().st_size for p in store_dir.glob("*")
                              if p.is_file()) if store_dir.is_dir() else 0
            rows.append({
                "case": case["case"],
                "note": case["note"],
                "original_bytes": len(text.encode("utf-8")),
                "visible_bytes": visible_bytes,
                "stored": stored,
                "clue": clue,
                "clue_visible_in_compressed": visible_has_clue,
                "recovery_calls": calls,
                "recovery_status": status,
                "recovery_response_bytes": response_bytes,
                "recovery_content_bytes": content_bytes,
                "clue_recovered": recovered,
                "bytes_to_clue": visible_bytes + response_bytes,
                "store_bytes": store_bytes,
            })
        return rows
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
        shutil.rmtree(home, ignore_errors=True)


def environment() -> "dict":
    """The conditions a reader needs to decide whether a timing is comparable.

    Collected without network access and without failing the run: a platform
    that lacks ``/proc`` or ``git`` simply records ``None`` for that field.
    """
    info: dict = {
        "timestamp_utc": datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_1min": None,
        "total_memory_bytes": None,
        "git_sha": None,
        "git_dirty": None,
    }
    try:
        info["load_1min"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):  # no load average on this platform
        pass
    try:
        info["total_memory_bytes"] = (
            os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        pass
    try:
        info["git_sha"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=10).stdout.strip() or None
        info["git_dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def load_policy(info: "dict", limit: float = LOAD_PER_CPU_LIMIT) -> "tuple[bool, str]":
    """Can this machine's timings be called representative?

    Deliberately conservative: one runnable task per CPU is the line. The
    harness never makes a machine idle by stopping other work; it defers.
    """
    load = info.get("load_1min")
    cpus = info.get("cpu_count")
    if load is None or not cpus:
        return False, "load average or CPU count is unavailable on this platform"
    per_cpu = load / cpus
    if per_cpu > limit:
        return False, (
            f"1-min load {load:.2f} on {cpus} CPUs is {per_cpu:.2f} per CPU "
            f"(limit {limit:.2f}); a representative timing needs an idle machine")
    return True, (f"1-min load {load:.2f} on {cpus} CPUs is {per_cpu:.2f} per CPU "
                  f"(limit {limit:.2f})")


def timed(call, *, warmups: int = TIMING_WARMUPS,
          repeats: int = TIMING_REPEATS) -> "dict":
    """Time one callable, keeping every trial rather than a cherry-picked best."""
    for _ in range(max(0, warmups)):
        call()
    trials = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        trials.append(round((time.perf_counter() - started) * 1000, 3))
    return {
        "median_ms": round(statistics.median(trials), 3),
        "min_ms": min(trials),
        "max_ms": max(trials),
        "trials_ms": trials,
    }


def _rss_kib() -> "int | None":
    """Resident set size of this process, where the platform exposes it."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def measure_memory(text: str, case: str) -> "dict":
    """Two different memory numbers, kept apart on purpose.

    ``peak_heap_kib`` is tracemalloc's peak Python allocation during the call
    (the input string already exists, so it is not included). ``rss_delta_kib``
    is the process resident set before and after the same call: it includes
    allocator and page-cache effects, and can be negative. Neither is the size
    of the payload and neither is total machine memory.
    """
    before = _rss_kib()
    tracemalloc.start()
    sutra.process_result({"output": text}, tool_name="terminal")
    _, peak = tracemalloc.get_traced_memory()  # (current, peak), not the reverse
    tracemalloc.stop()
    after = _rss_kib()
    return {
        "case": case,
        "input_bytes": len(text.encode("utf-8")),
        "peak_heap_kib": round(peak / 1024, 1),
        "rss_before_kib": before,
        "rss_after_kib": after,
        "rss_delta_kib": (None if before is None or after is None
                          else after - before),
    }


def measure_timing(*, repeats: int = TIMING_REPEATS) -> "dict":
    """Wall clock per phase: compression, retention, the full rewrite, retrieval.

    These are separate numbers because they answer separate questions. The
    store write is the cost of keeping the original recoverable; ``rewrite`` is
    what a host actually runs (classify, compress, retain, compress again so
    the marker can quote a real id); ``restore`` is the price of asking for the
    evidence back.
    """
    rows = []
    previous_home = os.environ.get("HERMES_HOME")
    home = tempfile.mkdtemp(prefix="sutra-timing-")
    os.environ["HERMES_HOME"] = home
    try:
        store = sutra.store.Store(
            sutra.store.StoreConfig.from_config(sutra.configuration()))
        for case in build_payloads():
            text = case["text"]
            clue = case["clue"]
            ops = {
                "compress": timed(lambda t=text: sutra.compress(t), repeats=repeats),
                # A fresh id and two atomic writes per call: this is the real
                # retention cost, not a cache hit.
                "store_put": timed(
                    lambda t=text: store.put(t, tool_name="bench"), repeats=repeats),
                "rewrite": timed(
                    lambda t=text: sutra.process_result(
                        {"output": t}, tool_name="terminal"), repeats=repeats),
            }
            result = sutra.process_result({"output": text}, tool_name="terminal")
            visible = result["output"] if isinstance(result, dict) else result
            entry = ID_RE.search(visible)
            restore = None
            if entry is not None and clue and clue not in visible:
                request = {"id": entry.group(1), "mode": "search", "query": clue,
                           "max_matches": 1, "context": 1}
                payload = json.loads(sutra.restore(request))
                restore = timed(lambda r=request: sutra.restore(r), repeats=repeats)
                restore["status"] = payload.get("status")
                restore["recovered"] = clue in payload.get("content", "")
            rows.append({
                "case": case["case"],
                "input_bytes": len(text.encode("utf-8")),
                "ops": ops,
                "restore": restore,
            })
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
        shutil.rmtree(home, ignore_errors=True)

    rewritten = {r["case"] for r in measure() if r["saved_bytes"] > 0}
    biggest = max((c for c in build_payloads() if c["case"] in rewritten),
                  key=lambda c: len(c["text"]))
    previous_home = os.environ.get("HERMES_HOME")
    home = tempfile.mkdtemp(prefix="sutra-mem-")
    os.environ["HERMES_HOME"] = home
    try:
        memory = measure_memory(biggest["text"], biggest["case"])
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
        shutil.rmtree(home, ignore_errors=True)
    return {"cases": rows, "memory": memory}


# --- the charts ------------------------------------------------------------

CHART_W = 900
ROW_H = 54
PAD_TOP = 96
PAD_LEFT = 250
PAD_RIGHT = 120
PAD_BOTTOM = 54
BAR_H = 15
BAR_GAP = 5
FONT = "ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,monospace"


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} kB"
    return f"{n} B"


def _header(title: str, subtitle: str, height: int, width: int = CHART_W) -> "list[str]":
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="28" y="40" font-family="{FONT}" font-size="21" font-weight="600" '
        f'fill="#0f172a">{title}</text>',
        f'<text x="28" y="66" font-family="{FONT}" font-size="13" fill="#64748b">'
        f'{subtitle}</text>',
        f'<line x1="{PAD_LEFT}" y1="{PAD_TOP - 16}" x2="{width - PAD_RIGHT}" '
        f'y2="{PAD_TOP - 16}" stroke="#e2e8f0" stroke-width="1"/>',
    ]


def render_svg(rows: "list[dict]") -> str:
    height = PAD_TOP + ROW_H * len(rows) + PAD_BOTTOM
    plot_w = CHART_W - PAD_LEFT - PAD_RIGHT
    peak = max(r["before_bytes"] for r in rows)

    def width(value: int) -> float:
        return plot_w * (value / peak)

    parts = _header(
        "What SUTRA saves on real tool output",
        "measured by bench/benchmark.py — before (grey) and after (blue) the middleware, bytes",
        height)
    for i, row in enumerate(rows):
        y = PAD_TOP + i * ROW_H
        parts += [
            f'<text x="{PAD_LEFT - 18}" y="{y + 14}" text-anchor="end" '
            f'font-family="{FONT}" font-size="13.5" fill="#0f172a">{row["case"]}</text>',
            f'<text x="{PAD_LEFT - 18}" y="{y + 31}" text-anchor="end" '
            f'font-family="{FONT}" font-size="11.5" fill="#94a3b8">{row["note"]}</text>',
            f'<rect x="{PAD_LEFT}" y="{y + 2}" width="{width(row["before_bytes"]):.1f}" '
            f'height="{BAR_H}" rx="2" fill="#cbd5e1"/>',
            f'<rect x="{PAD_LEFT}" y="{y + 2 + BAR_H + BAR_GAP}" '
            f'width="{width(row["after_bytes"]):.1f}" height="{BAR_H}" rx="2" fill="#2563eb"/>',
            f'<text x="{PAD_LEFT + max(width(row["before_bytes"]), width(row["after_bytes"])) + 10:.1f}" '
            f'y="{y + 15}" font-family="{MONO}" font-size="11.5" fill="#475569">'
            f'{human(row["before_bytes"])}</text>',
        ]
        if row["saved_bytes"] > 0:
            parts.append(
                f'<text x="{PAD_LEFT + width(row["after_bytes"]) + 10:.1f}" '
                f'y="{y + 2 + BAR_H + BAR_GAP + 12}" font-family="{MONO}" font-size="11.5" '
                f'fill="#1d4ed8">{human(row["after_bytes"])} · {row["saved_pct"]}% saved</text>')
        else:
            parts.append(
                f'<text x="{PAD_LEFT + width(row["after_bytes"]) + 10:.1f}" '
                f'y="{y + 2 + BAR_H + BAR_GAP + 12}" font-family="{MONO}" font-size="11.5" '
                f'fill="#b45309">left intact</text>')
    total_before = sum(r["before_bytes"] for r in rows)
    total_after = sum(r["after_bytes"] for r in rows)
    total_pct = round(100 * (total_before - total_after) / total_before, 1)
    parts.append(
        f'<text x="{CHART_W - PAD_RIGHT + 8}" y="{height - 22}" text-anchor="end" '
        f'font-family="{FONT}" font-size="12.5" fill="#0f172a" font-weight="600">'
        f'{human(total_before)} → {human(total_after)} across all {len(rows)} cases '
        f'({total_pct}% saved)</text>')
    parts.append("</svg>")
    return "\n".join(parts)


RECOVERY_ROW_H = 62
RECOVERY_BAR_H = 11
RECOVERY_BAR_GAP = 4


def render_recovery_svg(rows: "list[dict]") -> str:
    height = PAD_TOP + RECOVERY_ROW_H * len(rows) + PAD_BOTTOM
    plot_w = CHART_W - PAD_LEFT - PAD_RIGHT
    peak = max(max(r["original_bytes"], r["bytes_to_clue"]) for r in rows)

    def width(value: int) -> float:
        return plot_w * (value / peak)

    parts = _header(
        "The price of evidence: visible bytes, then a targeted retrieval",
        "grey = original, blue = what the model sees, amber = bytes to reach a buried clue "
        "after sutra_restore",
        height)
    for i, row in enumerate(rows):
        y = PAD_TOP + i * RECOVERY_ROW_H
        parts += [
            f'<text x="{PAD_LEFT - 18}" y="{y + 14}" text-anchor="end" '
            f'font-family="{FONT}" font-size="13.5" fill="#0f172a">{row["case"]}</text>',
            f'<text x="{PAD_LEFT - 18}" y="{y + 31}" text-anchor="end" '
            f'font-family="{FONT}" font-size="11.5" fill="#94a3b8">{row["note"]}</text>',
            f'<rect x="{PAD_LEFT}" y="{y + 2}" width="{width(row["original_bytes"]):.1f}" '
            f'height="{RECOVERY_BAR_H}" rx="2" fill="#cbd5e1"/>',
            f'<rect x="{PAD_LEFT}" y="{y + 2 + RECOVERY_BAR_H + RECOVERY_BAR_GAP}" '
            f'width="{width(row["visible_bytes"]):.1f}" height="{RECOVERY_BAR_H}" rx="2" '
            f'fill="#2563eb"/>',
            f'<rect x="{PAD_LEFT}" y="{y + 2 + 2 * (RECOVERY_BAR_H + RECOVERY_BAR_GAP)}" '
            f'width="{width(row["bytes_to_clue"]):.1f}" height="{RECOVERY_BAR_H}" rx="2" '
            f'fill="#f59e0b"/>',
            f'<text x="{PAD_LEFT + max(width(row["original_bytes"]), width(row["bytes_to_clue"])) + 10:.1f}" '
            f'y="{y + 12}" font-family="{MONO}" font-size="11" fill="#475569">'
            f'{human(row["original_bytes"])} original</text>',
            f'<text x="{PAD_LEFT + width(row["bytes_to_clue"]) + 10:.1f}" y="{y + 42}" '
            f'font-family="{MONO}" font-size="11" fill="#b45309">'
            f'{human(row["bytes_to_clue"])} to the clue'
            f'{" · " + str(row["recovery_calls"]) + " call" if row["recovery_calls"] else ""}'
            f'</text>',
        ]
    unrecovered = [r["case"] for r in rows if not r["clue_recovered"]]
    footer = ("every buried clue was retrieved" if not unrecovered
              else "NOT retrieved: " + ", ".join(unrecovered))
    parts.append(
        f'<text x="{CHART_W - PAD_RIGHT + 8}" y="{height - 22}" text-anchor="end" '
        f'font-family="{FONT}" font-size="12.5" fill="#0f172a" font-weight="600">{footer}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def verify_artifacts(out_dir: "pathlib.Path | str") -> "list[str]":
    """Check that artifacts agree with each other and with a fresh measurement.

    The docs tables and the SVG footers are claims. If a regenerated number
    disagrees with an artifact, either the harness is wrong or an artifact was
    hand-edited without a re-run; both deserve a non-zero exit.
    """
    out = pathlib.Path(out_dir)
    problems: "list[str]" = []

    def load(name: str):
        try:
            return json.loads((out / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{name}: {exc}")
            return None

    results = load("results.json")
    recovery = load("recovery.json")
    timing = load("timing.json")
    try:
        savings = (out / "savings.svg").read_text(encoding="utf-8")
        recovery_svg = (out / "recovery.svg").read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"svg: {exc}")
        savings = recovery_svg = ""

    if results is not None:
        if results != measure():
            problems.append(
                "results.json does not match a fresh deterministic measurement")
        total_before = sum(r["before_bytes"] for r in results)
        total_after = sum(r["after_bytes"] for r in results)
        pct = round(100 * (total_before - total_after) / total_before, 1)
        footer = f"{human(total_before)} → {human(total_after)}"
        for row in results:
            if row["case"] not in savings:
                problems.append(f"savings.svg is missing {row['case']}")
            if row["before_bytes"] - row["after_bytes"] != row["saved_bytes"]:
                problems.append(f"{row['case']}: saved_bytes is not before - after")
            if not 0.0 <= row["saved_pct"] <= 100.0:
                problems.append(f"{row['case']}: saved_pct outside 0-100")
        if footer not in savings or f"({pct}% saved)" not in savings:
            problems.append("savings.svg footer disagrees with results.json")

    if recovery is not None:
        by_case = {r["case"]: r for r in results or []}
        for row in recovery:
            if row["bytes_to_clue"] != row["visible_bytes"] + row["recovery_response_bytes"]:
                problems.append(
                    f"{row['case']}: bytes_to_clue is not visible + response")
            if row["recovery_calls"] and row["recovery_status"] != "ok":
                problems.append(
                    f"{row['case']}: a call was made but status is {row['recovery_status']!r}")
            if not row["clue_recovered"]:
                problems.append(f"{row['case']}: planted clue was not recovered")
            expected = by_case.get(row["case"], {}).get("before_bytes")
            if expected is not None and row["original_bytes"] != expected:
                problems.append(
                    f"{row['case']}: original_bytes disagrees with results.json")
            if row["case"] not in recovery_svg:
                problems.append(f"recovery.svg is missing {row['case']}")
        if all(r["clue_recovered"] for r in recovery) and \
                "every buried clue was retrieved" not in recovery_svg:
            problems.append("recovery.svg footer disagrees with recovery.json")

    if timing is None:
        return problems
    if timing.get("status") not in ("measured", "deferred"):
        problems.append("timing.json has no valid status")
    if timing.get("status") == "measured":
        repeats = timing.get("method", {}).get("repeats")
        for row in timing.get("cases", []):
            for name, op in row.get("ops", {}).items():
                trials = op.get("trials_ms", [])
                if repeats is not None and len(trials) != repeats:
                    problems.append(
                        f"{row['case']} {name}: {len(trials)} trials, expected {repeats}")
                if not op["min_ms"] <= op["median_ms"] <= op["max_ms"]:
                    problems.append(
                        f"{row['case']} {name}: median outside min/max")
    elif timing.get("representative"):
        problems.append("timing.json is deferred but marked representative")
    return problems


def _write(path: pathlib.Path, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _print_tables(rows: "list[dict]", recovery: "list[dict]", timing: "dict") -> None:
    print("| payload | rule | before | after | saved |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f'| {r["case"]} | `{r["rule"]}` | {human(r["before_bytes"])} | '
              f'{human(r["after_bytes"])} | {r["saved_pct"]}% |')
    tb = sum(r["before_bytes"] for r in rows)
    ta = sum(r["after_bytes"] for r in rows)
    print(f"\ntotal: {human(tb)} → {human(ta)} "
          f"({round(100 * (tb - ta) / tb, 1)}% saved)")

    print("\n| payload | visible | calls | response | to the clue | clue recovered |")
    print("|---|---|---|---|---|---|")
    for r in recovery:
        print(f'| {r["case"]} | {human(r["visible_bytes"])} | {r["recovery_calls"]} | '
              f'{human(r["recovery_response_bytes"])} | {human(r["bytes_to_clue"])} | '
              f'{"yes" if r["clue_recovered"] else "NO"} |')

    if timing["status"] == "measured":
        print("\n| payload | compress | store write | full rewrite | restore |")
        print("|---|---|---|---|---|")
        for row in timing["cases"]:
            ops = row["ops"]
            restore = row.get("restore")
            restore_cell = f'{restore["median_ms"]} ms' if restore else "n/a"
            print(f'| {row["case"]} | {ops["compress"]["median_ms"]} ms | '
                  f'{ops["store_put"]["median_ms"]} ms | '
                  f'{ops["rewrite"]["median_ms"]} ms | {restore_cell} |')
        print(f'\nrepresentative: {"yes" if timing["representative"] else "NO"} — '
              f'{timing["reason"]}')
        memory = timing.get("memory")
        if memory:
            print(f'peak Python heap while rewriting {memory["case"]}: '
                  f'{memory["peak_heap_kib"]} KiB (tracemalloc, input excluded)')
            if memory.get("rss_delta_kib") is not None:
                print(f'process RSS across the same call: '
                      f'{memory["rss_delta_kib"]} KiB '
                      f'({memory["rss_before_kib"]} → {memory["rss_after_kib"]} KiB)')
    else:
        print(f'\ntiming {timing["status"]}: {timing["reason"]}')
        print("no timing table is published for this run; re-run on an idle machine, "
              "or use --timing force to record a contended one")


def main(argv: "list[str] | None" = None,
         out_dir: "pathlib.Path | str | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timing", choices=("auto", "force", "off"), default="auto",
                        help="auto defers on a loaded machine; force records a contended run")
    parser.add_argument("--repeats", type=int, default=TIMING_REPEATS)
    parser.add_argument("--check", action="store_true",
                        help="verify the existing artifacts; write nothing")
    parser.add_argument("--out", default=None,
                        help="write artifacts here instead of bench/ (isolated runs)")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    out = pathlib.Path(args.out) if args.out else (
        pathlib.Path(out_dir) if out_dir is not None else ROOT / "bench")

    if args.check:
        problems = verify_artifacts(out)
        for problem in problems:
            print(f"INCONSISTENT: {problem}")
        print("artifacts are consistent" if not problems
              else f"{len(problems)} problem(s) in {out}")
        return 1 if problems else 0

    rows = measure()
    recovery = measure_recovery()
    info = environment()
    if args.timing == "off":
        status, reason = "deferred", "timing disabled with --timing off"
    elif args.timing == "force":
        status, reason = "measured", "forced with --timing force: not representative"
    else:
        run, reason = load_policy(info)
        status = "measured" if run else "deferred"
    timing = {
        "status": status,
        "representative": status == "measured" and args.timing == "auto",
        "reason": reason,
        "environment": info,
        "method": {
            "warmups": TIMING_WARMUPS,
            "repeats": args.repeats,
            "statistic": "median",
            "clock": "time.perf_counter",
            "load_limit_per_cpu": LOAD_PER_CPU_LIMIT,
        },
    }
    if status == "measured":
        timing.update(measure_timing(repeats=args.repeats))

    out.mkdir(parents=True, exist_ok=True)
    _write(out / "results.json", rows)
    _write(out / "recovery.json", recovery)
    _write(out / "timing.json", timing)
    _write(out / "savings.svg", render_svg(rows))
    _write(out / "recovery.svg", render_recovery_svg(recovery))

    _print_tables(rows, recovery, timing)
    problems = verify_artifacts(out)
    for problem in problems:
        print(f"INCONSISTENT: {problem}")
    print(f"\nwrote {out} ({'consistent' if not problems else 'INCONSISTENT'})")
    return 0 if not problems and all(r["clue_recovered"] for r in recovery) else 1


if __name__ == "__main__":
    sys.exit(main())
