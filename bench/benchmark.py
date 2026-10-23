#!/usr/bin/env python3
"""Measure tooltrim on representative tool output and draw the result.

Run from the repository root:

    python3 bench/benchmark.py

Writes ``bench/results.json`` and ``bench/savings.svg``, and prints a markdown
table with the same numbers. Everything here is deterministic: the payloads are
generated, the compressor is the real plugin, and nothing touches the network.

The point of the benchmark is not the biggest possible number. It is that every
row is checkable: run the command, get the same table.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tooltrim_plugin", ROOT / "__init__.py")
tooltrim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tooltrim)


def build_payloads() -> "list[tuple[str, str, str]]":
    """(label, note, text) for each measured case."""

    lines = "\n".join(str(i) for i in range(1, 12_001))

    json_envelope = json.dumps(
        {"output": "\n".join(f"line {i} of a long command" for i in range(1, 3_001)), "exit_code": 0}
    )

    test_run = "\n".join(
        ("\u2713 src/lib/editorial.test.ts > composes a story\n" * 3
         if i % 3 == 0 else f"\u2713 src/lib/module-{i}.test.ts > case {i} passes\n")
        for i in range(2_000)
    )

    traceback = "\n".join(f'  File "/app/src/module_{i}.py", line {i}, in handler_{i}' for i in range(3_000))
    traceback += "\nTraceback (most recent call last):\n" + traceback + "\nValueError: the thing broke\n"

    patch = "diff --git a/src/big.ts b/src/big.ts\n--- a/src/big.ts\n+++ b/src/big.ts\n@@ -1,2 +1,2 @@\n" + (
        "+const line = 'added by the patch'\n-const line = 'removed by the patch'\n" * 6_000
    )

    sql = "id\temail\tcreated_at\tstatus\n" + "\n".join(
        f"{i}\tuser{i}@example.com\t2026-10-{i % 28 + 1:02d}T09:00:00Z\tactive" for i in range(1, 901)
    )

    return [
        ("12,000-line command output", "plain text, head + tail", lines),
        ("JSON tool envelope", "decoded, string fields trimmed", json_envelope),
        ("test run, 2,000 cases", "repeat collapse + head/tail", test_run),
        ("3,000-frame traceback", "error end preserved", traceback),
        ("600 KB patch", "never rewritten (safety case)", patch),
        ("900-row SQL result", "head + tail", sql),
    ]


def measure() -> "list[dict]":
    rows = []
    for label, note, text in build_payloads():
        out, stats = tooltrim.compress(text)
        before = len(text.encode("utf-8"))
        after = len(out.encode("utf-8"))
        if after > 0 and stats["rule"] == "diff-kept":
            after = before  # diff path returns the original by design
        rows.append({
            "case": label,
            "note": note,
            "rule": stats["rule"],
            "before_bytes": before,
            "after_bytes": after,
            "saved_bytes": before - after,
            "saved_pct": round(100 * (before - after) / before, 1) if before else 0.0,
        })
    return rows


# --- the chart -------------------------------------------------------------

CHART_W = 900
ROW_H = 54
PAD_TOP = 96
PAD_LEFT = 250
PAD_RIGHT = 120
PAD_BOTTOM = 54
BAR_H = 15
BAR_GAP = 5


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} kB"
    return f"{n} B"


def render_svg(rows: "list[dict]") -> str:
    height = PAD_TOP + ROW_H * len(rows) + PAD_BOTTOM
    plot_w = CHART_W - PAD_LEFT - PAD_RIGHT
    peak = max(r["before_bytes"] for r in rows)

    def width(value: int) -> float:
        return plot_w * (value / peak)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_W}" height="{height}" '
        f'viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-label="Bytes before and after tooltrim, per payload">',
        f'<rect width="{CHART_W}" height="{height}" fill="#ffffff"/>',
        f'<text x="28" y="40" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif" '
        f'font-size="21" font-weight="600" fill="#0f172a">What tooltrim saves on real tool output</text>',
        f'<text x="28" y="66" font-family="ui-sans-serif,system-ui,sans-serif" font-size="13" fill="#64748b">'
        f'measured by bench/benchmark.py — before (grey) and after (blue) the middleware, bytes</text>',
        f'<line x1="{PAD_LEFT}" y1="{PAD_TOP - 16}" x2="{CHART_W - PAD_RIGHT}" y2="{PAD_TOP - 16}" '
        f'stroke="#e2e8f0" stroke-width="1"/>',
    ]

    for i, row in enumerate(rows):
        y = PAD_TOP + i * ROW_H
        label = row["case"]
        parts.append(
            f'<text x="{PAD_LEFT - 18}" y="{y + 14}" text-anchor="end" '
            f'font-family="ui-sans-serif,system-ui,sans-serif" font-size="13.5" fill="#0f172a">{label}</text>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 18}" y="{y + 31}" text-anchor="end" '
            f'font-family="ui-sans-serif,system-ui,sans-serif" font-size="11.5" fill="#94a3b8">{row["note"]}</text>'
        )
        # before
        parts.append(
            f'<rect x="{PAD_LEFT}" y="{y + 2}" width="{width(row["before_bytes"]):.1f}" height="{BAR_H}" '
            f'rx="2" fill="#cbd5e1"/>'
        )
        # after
        parts.append(
            f'<rect x="{PAD_LEFT}" y="{y + 2 + BAR_H + BAR_GAP}" width="{width(row["after_bytes"]):.1f}" '
            f'height="{BAR_H}" rx="2" fill="#2563eb"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT + max(width(row["before_bytes"]), width(row["after_bytes"])) + 10:.1f}" '
            f'y="{y + 15}" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11.5" '
            f'fill="#475569">{human(row["before_bytes"])}</text>'
        )
        if row["saved_bytes"] > 0:
            parts.append(
                f'<text x="{PAD_LEFT + width(row["after_bytes"]) + 10:.1f}" y="{y + 2 + BAR_H + BAR_GAP + 12}" '
                f'font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11.5" '
                f'fill="#1d4ed8">{human(row["after_bytes"])} \u00b7 {row["saved_pct"]}% saved</text>'
            )
        else:
            parts.append(
                f'<text x="{PAD_LEFT + width(row["after_bytes"]) + 10:.1f}" y="{y + 2 + BAR_H + BAR_GAP + 12}" '
                f'font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11.5" '
                f'fill="#b45309">left intact</text>'
            )

    total_before = sum(r["before_bytes"] for r in rows)
    total_after = sum(r["after_bytes"] for r in rows)
    total_pct = round(100 * (total_before - total_after) / total_before, 1)
    parts.append(
        f'<text x="{CHART_W - PAD_RIGHT + 8}" y="{height - 22}" text-anchor="end" '
        f'font-family="ui-sans-serif,system-ui,sans-serif" font-size="12.5" fill="#0f172a" font-weight="600">'
        f'{human(total_before)} \u2192 {human(total_after)} across all six cases ({total_pct}% saved)</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    rows = measure()
    out_dir = ROOT / "bench"
    (out_dir / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    (out_dir / "savings.svg").write_text(render_svg(rows), encoding="utf-8")

    print("| payload | rule | before | after | saved |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f'| {r["case"]} | `{r["rule"]}` | {human(r["before_bytes"])} | '
              f'{human(r["after_bytes"])} | {r["saved_pct"]}% |')
    tb = sum(r["before_bytes"] for r in rows)
    ta = sum(r["after_bytes"] for r in rows)
    print(f"\ntotal: {human(tb)} \u2192 {human(ta)} ({round(100 * (tb - ta) / tb, 1)}% saved)")
    print(f"wrote {out_dir / 'results.json'} and {out_dir / 'savings.svg'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
