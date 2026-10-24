#!/usr/bin/env python3
"""Render the A/B results into the frozen report shape — see docs/ab-benchmark.md.

Reads the runner's per-arm records (`results-<arm>-pass<n>.jsonl`) and emits a
markdown report that keeps the three evidence classes apart:

1. **Task completion** — objective pass/fail per task per arm, with sample
   sizes. The headline: does SUTRA change whether the task gets done?
2. **Measured usage** — provider-reported token totals per arm. Shown only
   where the provider reported it; never estimated, never extrapolated.
3. **SUTRA overhead and evidence** (treatment arm) — durations, markers the
   model saw, restore calls and their statuses.

Deviations (timeouts, errors, dry runs) are listed, never dropped. The report
states its own limits: a single pass is not a conclusion.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

ARMS = ("off", "on")


def load(paths: list[str]) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        with open(path) as fh:
            records.extend(json.loads(line) for line in fh if line.strip())
    return records


def fmt_int(value: int | float | None) -> str:
    if value is None:
        return "unreported"
    return f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)


def task_name(record: dict) -> str:
    return str(record.get("task"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", nargs="+", help="results-<arm>-pass<n>.jsonl files")
    parser.add_argument("--out", default=None, help="write markdown here (default stdout)")
    args = parser.parse_args()
    records = load(args.records)
    if not records:
        print("no records; nothing to report")
        return 1

    by_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        by_task[task_name(record)][str(record.get("arm"))].append(record)
    tasks = sorted(by_task)
    seeds = sorted({str(r.get("seed")) for r in records})
    dry = any(r.get("dry_run") for r in records)

    lines: list[str] = []
    lines.append("# A/B results — SUTRA on vs off\n")
    lines.append(f"- records: {len(records)} from {len(args.records)} file(s); "
                 f"tasks: {len(tasks)}; seeds: {', '.join(seeds)}")
    if dry:
        lines.append("- **contains dry-run records: these are mechanics checks, "
                     "not model results, and must not be read as either arm**")
    lines.append("- one pass per arm is not a conclusion; per docs/ab-benchmark.md, "
                 "report sample sizes and prefer the reversed-order second pass.\n")

    lines.append("## 1. Task completion (objective scorers)\n")
    lines.append("| task | off pass/total | on pass/total | timeouts (off/on) |")
    lines.append("|---|---|---|---|")
    for task in tasks:
        row = []
        for arm in ARMS:
            runs = by_task[task].get(arm, [])
            passed = sum(1 for r in runs if r.get("scored") is True and not r.get("timed_out"))
            row.append(f"{passed}/{len(runs)}")
        timeouts = "/".join(
            str(sum(1 for r in by_task[task].get(arm, []) if r.get("timed_out")))
            for arm in ARMS)
        lines.append(f"| {task} | {row[0]} | {row[1]} | {timeouts} |")
    lines.append("")

    lines.append("## 2. Measured provider usage (never estimated)\n")
    lines.append("| task | arm | input tokens | output tokens | cache read | api calls | reported? |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for task in tasks:
        for arm in ARMS:
            for record in by_task[task].get(arm, []):
                usage = (record.get("usage") or {}).get("total")
                if not usage:
                    lines.append(f"| {task} | {arm} | — | — | — | — | not reported by provider |")
                    continue
                lines.append(
                    f"| {task} | {arm} | {fmt_int(usage.get('input_tokens'))} | "
                    f"{fmt_int(usage.get('output_tokens'))} | "
                    f"{fmt_int(usage.get('cache_read_tokens'))} | "
                    f"{fmt_int(usage.get('api_call_count'))} | yes |")
    lines.append("\nUsage deltas are stated only where both arms of a task "
                 "reported usage, and carry the protocol's caching caveat.\n")

    lines.append("## 3. SUTRA overhead and evidence (treatment arm)\n")
    lines.append("| task | session duration (on) | markers model saw | restore calls | restore statuses |")
    lines.append("|---|---:|---:|---:|---|")
    for task in tasks:
        for record in by_task[task].get("on", []):
            evidence = record.get("sutra_evidence") or {}
            lines.append(
                f"| {task} | {record.get('duration_s', '—')}s | "
                f"{evidence.get('tool_results_with_marker', '—')} | "
                f"{evidence.get('restore_calls', 0)} | "
                f"{', '.join(map(str, evidence.get('restore_statuses', []))) or '—'} |")
    lines.append("")

    lines.append("## 4. Deviations\n")
    deviations = 0
    for record in records:
        notes = []
        if record.get("timed_out"):
            notes.append("timed out")
        if record.get("exit_code") not in (0, None) and not record.get("timed_out"):
            notes.append(f"exit {record.get('exit_code')}")
        if record.get("stderr_tail"):
            notes.append("stderr: " + record["stderr_tail"].strip().splitlines()[-1][:120])
        if notes:
            deviations += 1
            lines.append(f"- {record.get('arm')}/{task_name(record)} "
                         f"(seed {record.get('seed')}): {'; '.join(notes)}")
    if not deviations:
        lines.append("- none recorded")
    report = "\n".join(lines) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
