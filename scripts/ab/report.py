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
import math
import pathlib
import sys
from collections import defaultdict

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


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


# --- Phase Four additions: statistical honesty and cost -------------------
# Documented default pricing (USD per 1,000,000 tokens). These are planning
# estimates, not provider bills; real billing depends on the model/version the
# provider charges and is available only where the provider reports usage.
DEFAULT_MODEL = "gpt-4o"
DEFAULT_RATES = {
    DEFAULT_MODEL: {"input_per_1m": 2.50, "output_per_1m": 10.00},
}

_Z95 = 1.96  # two-sided 95% Wilson score interval


def ci_wilson(successes: int, trials: int, z: float = _Z95):
    """Wilson score confidence interval for a proportion.

    Returns None when there are no trials. Failures are never excluded.
    """
    if trials <= 0:
        return None
    phat = successes / trials
    denom = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denom
    half = (z / denom) * math.sqrt(
        (phat * (1 - phat) + z * z / (4 * trials)) / trials)
    return max(0.0, centre - half), min(1.0, centre + half)


def cost_usd(input_tokens, output_tokens, model=DEFAULT_MODEL):
    """Estimated cost from token counts using documented default rates.

    Returns None when token counts are missing; never fabricates a bill.
    """
    rates = DEFAULT_RATES.get(model)
    if rates is None and input_tokens is None and output_tokens is None:
        return None
    inn = input_tokens or 0
    out = output_tokens or 0
    if rates is None:
        return None
    return (inn * rates["input_per_1m"] + out * rates["output_per_1m"]) / 1_000_000


def recovery_calls(record: dict) -> int:
    evidence = record.get("sutra_evidence") or {}
    return int(evidence.get("restore_calls") or 0)


def runner_tokens(record: dict):
    rt = record.get("runner_tokens") or {}
    return rt.get("input"), rt.get("output")


def fmt_usd(value) -> str:
    if value is None:
        return "unreported"
    return f"{value:,.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", nargs="+", help="results-<arm>-pass<n>.jsonl files")
    parser.add_argument("--out", default=None, help="write markdown here (default stdout)")
    args = parser.parse_args(argv)
    records = load(args.records)
    if not records:
        print("no records; nothing to report")
        return 1

    by_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        by_task[task_name(record)][str(record.get("arm"))].append(record)
    tasks = sorted(by_task)
    arm_task_counts = [len(by_task[t].get(a, [])) for t in tasks for a in ARMS]
    if arm_task_counts:
        trials_lo, trials_hi = min(arm_task_counts), max(arm_task_counts)
    else:
        trials_lo = trials_hi = 0
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

    lines.append("### Completion rate by arm (Wilson 95% CI; failures included)\n")
    lines.append("| arm | completed/total | rate | 95% CI | failures | regressions vs other arm |")
    lines.append("|---|---:|---:|---|---:|---:|")
    regressions = 0
    for arm in ARMS:
        runs = [r for r in records if str(r.get("arm")) == arm]
        total = len(runs)
        passed = sum(1 for r in runs if r.get("scored") is True and not r.get("timed_out"))
        failures = sum(1 for r in runs if r.get("scored") is False)
        ci = ci_wilson(passed, total)
        ci_str = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "—"
        rate = passed / total * 100 if total else 0.0
        other = "on" if arm == "off" else "off"
        arm_regressions = sum(
            1 for t in tasks
            for r in by_task[t].get(arm, [])
            if r.get("scored") is False
            and any(o.get("scored") is True for o in by_task[t].get(other, [])))
        if arm == "on":
            regressions = arm_regressions
        reg_str = str(arm_regressions) if arm == "on" else "—"
        lines.append(f"| {arm} | {passed}/{total} | {rate:.1f}% | {ci_str} | "
                     f"{failures} | {reg_str} |")
    lines.append(f"Regression rate (treatment fail, control pass): "
                 f"{regressions}/{len(tasks)} tasks.\n")

    lines.append("## 2. Tokens, tool calls, recovery calls, time and cost")
    lines.append(
        "| task | arm | in tok (runner) | out tok (runner) | in tok (provider) | "
        "out tok (provider) | tool calls | recovery calls | duration (s) | "
        "est. cost ($) | provider-usage cost ($) | actual cost ($) | reported? |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for task in tasks:
        for arm in ARMS:
            for record in by_task[task].get(arm, []):
                usage = record.get("usage") or {}
                total_usage = usage.get("total")
                rin, rout = runner_tokens(record)
                est = cost_usd(rin, rout)
                prov_cost = (cost_usd(usage.get("input_tokens"),
                                      usage.get("output_tokens")))
                actual = total_usage.get("actual_cost_usd") if total_usage else None
                tool_calls = record.get("tool_calls", "—")
                if not total_usage:
                    lines.append(f"| {task} | {arm} | {fmt_int(rin)} | {fmt_int(rout)} | — | — | "
                                 f"{tool_calls} | {recovery_calls(record)} | "
                                 f"{record.get('duration_s', '—')} | {fmt_usd(est)} | "
                                 f"not reported | not reported | not reported |")
                    continue
                lines.append(f"| {task} | {arm} | {fmt_int(rin)} | {fmt_int(rout)} | "
                             f"{fmt_int(usage.get('input_tokens'))} | "
                             f"{fmt_int(usage.get('output_tokens'))} | "
                             f"{tool_calls} | {recovery_calls(record)} | "
                             f"{record.get('duration_s', '—')} | {fmt_usd(est)} | "
                             f"{fmt_usd(prov_cost)} | {fmt_usd(actual)} | yes |")
    lines.append("")
    lines.append("Estimated cost prices runner-counted tokens at documented default rates "
                 "(not a provider bill). Provider-usage cost prices provider-reported "
                 "tokens at the same default rates; actual cost is the provider's own "
                 "reported bill where the store records it. Both carry the "
                 "model/version pricing assumption stated in the statistical-honesty "
                 "section. Usage deltas are stated only where both arms of a task "
                 "reported usage, and carry the protocol's caching caveat.")
    lines.append("")
    lines.append("### Cost by arm (runner-counted estimates vs provider-reported)")
    lines.append("| arm | sessions | est. cost ($) | provider-usage cost ($) | actual cost ($) |")
    lines.append("|---|---:|---:|---:|---:|")
    for arm in ARMS:
        arm_runs = [r for r in records if str(r.get("arm")) == arm]
        sessions = len(arm_runs)
        est_total = 0.0
        prov_total = 0.0
        actual_total = 0.0
        provider_reported = False
        for record in arm_runs:
            rin, rout = runner_tokens(record)
            est_total += cost_usd(rin, rout) or 0.0
            usage = record.get("usage") or {}
            total_usage = usage.get("total") or {}
            if total_usage:
                # provider-reported usage exists for at least one session in
                # this arm; without it a cost total would be a fabrication.
                provider_reported = True
            prov_total += cost_usd(total_usage.get("input_tokens"),
                                   total_usage.get("output_tokens")) or 0.0
            actual_total += total_usage.get("actual_cost_usd") or 0.0
        lines.append(f"| {arm} | {sessions} | {fmt_usd(est_total)} | "
                     f"{fmt_usd(prov_total if provider_reported else None)} | "
                     f"{fmt_usd(actual_total if provider_reported else None)} |")
    lines.append("")

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

    lines.append("## 4. Statistical honesty")
    lines.append("| item | value |")
    lines.append("|---|---|")
    lines.append(f"| tasks | {len(tasks)} |")
    lines.append(f"| trials per arm per task | {trials_lo}–{trials_hi} recorded sessions |")
    lines.append(f"| total sessions | {len(records)} |")
    lines.append(f"| confidence interval | Wilson score, 95% (z = {_Z95}) |")
    lines.append("| completion rate basis | objective scorer; failures and timeouts included, never excluded |")
    lines.append(f"| cost model | documented default rates for {DEFAULT_MODEL}; estimates only, not provider bills |")
    live = "live" if any(r.get("usage") for r in records) else "not available; provider-reported usage unreported"
    lines.append(f"| provider access | {live} |")
    lines.append("")
    lines.append("Deterministic compression savings (fixture byte reduction) are measured by "
                 "sutra itself and are independent of any provider. Real provider billing "
                 "savings are the reduction in provider-reported billed tokens between the "
                 "control (off) and treatment (on) arms; this report prices both at documented "
                 "default rates, so cost figures are estimates. Model, version, provider, task "
                 "selection and repetition counts are limitations: results apply to these tasks "
                 "and this configuration, not to arbitrary workloads.")
    lines.append("")


    lines.append("## 5. Deviations\n")
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
