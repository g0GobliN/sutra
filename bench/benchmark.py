#!/usr/bin/env python3
"""Measure SUTRA on representative tool output and draw the result.

Run from the repository root:

    python3 bench/benchmark.py

Writes ``bench/results.json``, ``bench/recovery.json``, ``bench/savings.svg``
and ``bench/recovery.svg``, and prints two markdown tables with the same
numbers. Everything here is deterministic: the payloads are generated, the
compressor and the store are the real plugin, and nothing touches the network.

What is measured: bytes, recovery calls, whether a buried clue is reachable,
and the disk cost of retention. What is *not* measured here: model input
tokens, provider cost and task success — those need a live model and a task
harness; see docs/benchmark.md. No token number in this repository is derived
from a byte count.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
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


def measure_overhead(repeats: int = 3) -> "list[dict]":
    """Wall-clock cost of one rewrite, without and with retention, and peak memory."""
    rows = []
    for case in build_payloads():
        text = case["text"]
        # Warm up: the first call in a fresh process pays for filesystem and
        # entropy set-up that is not part of steady-state processing.
        sutra.compress(text)
        plain = []
        for _ in range(repeats):
            started = time.perf_counter()
            sutra.compress(text)
            plain.append((time.perf_counter() - started) * 1000)
        previous_home = os.environ.get("HERMES_HOME")
        home = tempfile.mkdtemp(prefix="sutra-timing-")
        os.environ["HERMES_HOME"] = home
        try:
            sutra.compress_result({"output": text}, tool_name="terminal")
            stored = []
            for _ in range(repeats):
                started = time.perf_counter()
                sutra.compress_result({"output": text}, tool_name="terminal")
                stored.append((time.perf_counter() - started) * 1000)
        finally:
            if previous_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = previous_home
            shutil.rmtree(home, ignore_errors=True)
        rows.append({
            "case": case["case"],
            "input_bytes": len(text.encode("utf-8")),
            "compress_ms": round(min(plain), 2),
            "with_store_ms": round(min(stored), 2),
        })
    rewritten = {r["case"] for r in measure() if r["saved_bytes"] > 0}
    biggest = max((c for c in build_payloads() if c["case"] in rewritten),
                  key=lambda c: len(c["text"]))
    previous_home = os.environ.get("HERMES_HOME")
    home = tempfile.mkdtemp(prefix="sutra-mem-")
    os.environ["HERMES_HOME"] = home
    try:
        tracemalloc.start()
        sutra.compress_result({"output": biggest["text"]}, tool_name="terminal")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
        shutil.rmtree(home, ignore_errors=True)
    rows.append({
        "case": f"peak allocation while rewriting {biggest['case']}",
        "input_bytes": len(biggest["text"].encode("utf-8")),
        "peak_kib": round(peak / 1024, 1),
    })
    return rows


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


def main() -> int:
    rows = measure()
    recovery = measure_recovery()
    overhead = measure_overhead()
    out_dir = ROOT / "bench"
    (out_dir / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    (out_dir / "recovery.json").write_text(json.dumps(recovery, indent=2) + "\n", encoding="utf-8")
    (out_dir / "timing.json").write_text(json.dumps(overhead, indent=2) + "\n", encoding="utf-8")
    (out_dir / "savings.svg").write_text(render_svg(rows), encoding="utf-8")
    (out_dir / "recovery.svg").write_text(render_recovery_svg(recovery), encoding="utf-8")

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
    print("\n| payload | compress | compress + store |")
    print("|---|---|---|")
    for r in overhead:
        if "compress_ms" in r:
            print(f'| {r["case"]} | {r["compress_ms"]} ms | {r["with_store_ms"]} ms |')
    for r in overhead:
        if "peak_kib" in r:
            print(f'\n{r["case"]}: {r["peak_kib"]} KiB additional heap for '
                  f'{human(r["input_bytes"])} of input (tracemalloc, excludes the input)')
    print(f"\nwrote {out_dir / 'results.json'}, {out_dir / 'recovery.json'}, "
          f"{out_dir / 'timing.json'}, {out_dir / 'savings.svg'}, {out_dir / 'recovery.svg'}")
    return 0 if all(r["clue_recovered"] for r in recovery) else 1


if __name__ == "__main__":
    sys.exit(main())
