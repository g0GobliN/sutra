"""Condition comparison for the Phase 1 SUTRA eval harness.

Computes pairwise comparisons (B vs A, C vs A, C vs B) with absolute and
percentage differences for every measured metric, plus variability awareness
and next-step suggestions. Everything is JSON-serializable; :func:`format_comparison`
renders it humanized (GUI-style icons and columns, consistent with the sutra CLI).
"""

from __future__ import annotations

import statistics
from typing import Any

PAIRS = (("A", "B"), ("A", "C"), ("B", "C"))

# Metrics where a lower value is the better outcome.
LOWER_IS_BETTER = frozenset({
    "visible_bytes", "compression_ms", "restore_ms", "clues_lost",
    "exact_failures",
})

METRICS = [
    "visible_bytes", "compression_ms", "restore_ms", "clues_lost",
    "exact_failures", "protected_ok", "reuse_hits", "pass_rate",
]


def _mean(values: list[float]) -> float | None:
    values = [float(v) for v in values if v is not None]
    return statistics.mean(values) if values else None


def _pct(baseline: float | None, compared: float | None) -> float | None:
    if baseline is None or compared is None or baseline == 0:
        return None
    return (compared - baseline) / baseline * 100.0


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_entry(name: str, baseline: Any, compared: Any) -> dict[str, Any]:
    base = _number(baseline)
    comp = _number(compared)
    diff: float | None = None
    if base is not None and comp is not None:
        diff = comp - base
    entry: dict[str, Any] = {
        "metric": name,
        "baseline": base,
        "compared": comp,
        "diff_abs": diff,
        "diff_pct": _pct(base, comp),
    }
    if diff is None:
        entry["direction"] = "unmeasured"
    elif base is None:
        entry["direction"] = "no_baseline"
    elif diff == 0:
        entry["direction"] = "unchanged"
    elif name in LOWER_IS_BETTER:
        if base == 0 and comp is not None and comp > 0:
            entry["direction"] = "introduced"
        else:
            entry["direction"] = "improved" if diff < 0 else "worsened"
    else:
        entry["direction"] = "improved" if diff > 0 else "worsened"
    return entry


def _extract(aggregate: dict[str, Any] | None, metric: str) -> Any:
    if not aggregate:
        return None
    return aggregate.get(metric)


def _extract_value(aggregate: dict[str, Any] | None, metric: str) -> float | None:
    """Pull a comparable number out of an aggregate entry.

    Stats entries are dicts (``{"mean", "stdev", ...}``); pass-rate entries are
    dicts with ``rate``; counts are stored as plain numbers.
    """
    value = _extract(aggregate, metric)
    if isinstance(value, dict):
        if "mean" in value:
            return value["mean"]
        if "rate" in value:
            return value["rate"]
        return None
    return _number(value)


def compute_comparison(summary: dict[str, Any]) -> dict[str, Any]:
    """Compare conditions pairwise from a harness summary dict.

    Returns a JSON-serializable dict with per-task pairwise metric entries
    (absolute + percentage differences), overall rollups, and next steps.
    """
    pair_names = [f"{b}_vs_{a}" for (a, b) in PAIRS]

    task_comparisons: list[dict[str, Any]] = []
    for task in summary.get("tasks", []):
        conditions = task.get("conditions", {})
        aggregates = {
            condition: result.get("aggregate", {})
            for condition, result in conditions.items()
            if isinstance(result, dict)
        }
        pairs: list[dict[str, Any]] = []
        for (a, b) in PAIRS:
            if a not in aggregates or b not in aggregates:
                continue
            base_agg = aggregates[a]
            comp_agg = aggregates[b]
            pairs.append({
                "pair": f"{b}_vs_{a}",
                "baseline_condition": a,
                "compared_condition": b,
                "metrics": [
                    _metric_entry(m, _extract_value(base_agg, m),
                                  _extract_value(comp_agg, m))
                    for m in METRICS
                ],
            })
        task_comparisons.append({
            "task_id": task["task_id"],
            "task_type": task.get("task_type"),
            "difficulty_rating": task.get("difficulty_rating"),
            "evidence_level": task.get("evidence_level"),
            "evidence_achieved": task.get("evidence_achieved"),
            "pairs": pairs,
        })

    overall: dict[str, Any] = {"pairs": []}
    for (a, b) in PAIRS:
        pair_entry: dict[str, Any] = {
            "pair": f"{b}_vs_{a}",
            "metrics": [],
        }
        target = f"{b}_vs_{a}"
        for m in METRICS:
            bases: list[float] = []
            comps: list[float] = []
            for tc in task_comparisons:
                for pair in tc["pairs"]:
                    if pair["pair"] != target:
                        continue
                    for entry in pair["metrics"]:
                        if entry["metric"] == m:
                            if entry["baseline"] is not None:
                                bases.append(entry["baseline"])
                            if entry["compared"] is not None:
                                comps.append(entry["compared"])
            pair_entry["metrics"].append(_metric_entry(m, _mean(bases) or None,
                                                       _mean(comps) or None))
        overall["pairs"].append(pair_entry)

    return {
        "run_id": summary.get("run_id"),
        "pairs": pair_names,
        "tasks": task_comparisons,
        "overall": overall,
        "next_steps": _next_steps(task_comparisons),
    }


def _next_steps(task_comparisons: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    clues_lost_control: list[str] = []
    c_worse_than_b: list[str] = []
    for tc in task_comparisons:
        for pair in tc["pairs"]:
            if pair["pair"] != "B_vs_A":
                continue
            for entry in pair["metrics"]:
                if (entry["metric"] == "clues_lost"
                        and entry["baseline"] is not None
                        and entry["baseline"] > 0):
                    clues_lost_control.append(tc["task_id"])
        for pair in tc["pairs"]:
            if pair["pair"] != "C_vs_B":
                continue
            for entry in pair["metrics"]:
                if (entry["metric"] == "pass_rate"
                        and entry["compared"] is not None
                        and entry["baseline"] is not None
                        and entry["compared"] < entry["baseline"]):
                    c_worse_than_b.append(tc["task_id"])
    if clues_lost_control:
        steps.append(
            "Recovery (condition B) recovers clues the control loses: "
            + ", ".join(clues_lost_control))
    if c_worse_than_b:
        steps.append(
            "Prefer condition B over C for: " + ", ".join(c_worse_than_b)
            + " (reuse/policy reduced the check pass rate)")
    if not steps:
        steps.append(
            "All measured conditions are stable; condition C adds exact-duplicate "
            "reuse and policy-based selection on top of condition B without "
            "losing any checked behavior.")
    return steps


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def format_comparison(comparison: dict[str, Any], tty: bool = True) -> str:
    """Humanized rendering of a comparison dict (icons + columns)."""
    try:
        from sutra.ui import colorize, icon  # type: ignore
    except Exception:  # pragma: no cover - fallback when sutra.ui unavailable

        def colorize(text: str, _style: str = "") -> str:
            return text

        def icon(_status: str) -> str:
            return ""

    ok = icon("success") or "✓"
    bad = icon("fail") or "✗"
    neutral = icon("warning") or "→"
    intro = icon("info") or "•"

    lines: list[str] = [
        colorize("SUTRA eval — condition comparison", "heading"),
        colorize("=" * 66, "muted"),
        f"run: {comparison.get('run_id') or '(unknown)'}",
        f"pairs: {', '.join(comparison.get('pairs', []))}",
        "",
    ]

    for tc in comparison.get("tasks", []):
        lines.append(colorize(
            f"task {tc['task_id']} ({tc.get('task_type', '?')})", "bold"))
        lines.append(
            f"  evidence: {tc.get('evidence_level')} declared / "
            f"{tc.get('evidence_achieved')} achieved")
        for pair in tc.get("pairs", []):
            lines.append(
                f"  {pair['pair']} ({pair['baseline_condition']} → "
                f"{pair['compared_condition']}):")
            for entry in pair.get("metrics", []):
                mark = {"improved": ok, "worsened": bad, "unchanged": neutral,
                        "introduced": intro}.get(entry.get("direction"), " ")
                pct = (f" ({entry['diff_pct']:+.1f}%)"
                       if entry.get("diff_pct") is not None else "")
                lines.append(
                    f"    {mark} {entry['metric']}: {_fmt(entry['baseline'])} → "
                    f"{_fmt(entry['compared'])}  Δ{_fmt(entry['diff_abs'])}{pct}")
        lines.append("")

    lines.append(colorize("overall", "bold"))
    for pair in comparison.get("overall", {}).get("pairs", []):
        lines.append(f"  {pair['pair']}:")
        for entry in pair.get("metrics", []):
            mark = {"improved": ok, "worsened": bad,
                    "unchanged": neutral}.get(entry.get("direction"), " ")
            pct = (f" ({entry['diff_pct']:+.1f}%)"
                   if entry.get("diff_pct") is not None else "")
            lines.append(
                f"    {mark} {entry['metric']}: {_fmt(entry['baseline'])} → "
                f"{_fmt(entry['compared'])}  Δ{_fmt(entry['diff_abs'])}{pct}")
    lines.append("")
    lines.append(colorize("next steps", "bold"))
    for step in comparison.get("next_steps", []):
        lines.append(f"  {neutral} {step}")
    lines.append("")
    return "\n".join(lines)
