"""Deterministic, model-free replay of representative Hermes workloads.

This harness answers a narrower question than a live A/B: what does SUTRA
change about the *tool-result context* an agent would read, and does every
required clue survive? It calls no model, spends no provider credits and makes
no provider-token or task-success claim.

For each workload it replays a fixed, recorded sequence of tool calls through
the real shared core, store and recovery seam under a temporary
``HERMES_HOME``. It measures:

- tool-result context tokens, defined as the sum over turns of every tool result
  visible so far (the part of the conversation SUTRA controls);
- original and visible bytes, compression and restoration latency;
- exact-duplicate reuse hits and the bytes they suppress;
- the policy section's token cost per turn, measured from the real text;
- whether each planted clue is visible, recoverable byte-exactly, or lost.

The policy's *behavioural* effect — whether an agent makes fewer tool calls — is
not modelled here. Tool sequences are identical across modes on purpose; a live
run is required to measure that effect. The report labels this boundary.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import pathlib
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Iterable

ROOT = pathlib.Path(__file__).resolve().parent.parent
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")
_PLUGIN = None
_ENCODER = None
_ENCODER_ERROR = None


def load_plugin():
    """Import the repository root as the plugin package, exactly as Hermes does."""
    global _PLUGIN
    if _PLUGIN is not None:
        return _PLUGIN
    name = "sutra_eval"
    if name in sys.modules:
        _PLUGIN = sys.modules[name]
        return _PLUGIN
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _PLUGIN = module
    return module


def encoder():
    """The o200k tokenizer, or None with the reason recorded."""
    global _ENCODER, _ENCODER_ERROR
    if _ENCODER is not None or _ENCODER_ERROR is not None:
        return _ENCODER
    try:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("o200k_base")
    except Exception as exc:  # benchmark-only dependency; never a hard failure
        _ENCODER_ERROR = f"{type(exc).__name__}: {exc}"
        _ENCODER = None
    return _ENCODER


def count_tokens(text: str) -> int | None:
    enc = encoder()
    return len(enc.encode(text)) if enc is not None else None


def tokenizer_note() -> str:
    enc = encoder()
    return "tiktoken 0.14.0/o200k_base" if enc is not None else f"unavailable ({_ENCODER_ERROR})"


@dataclass(frozen=True)
class Step:
    """One recorded tool call and the exact result the host would return."""

    tool: str
    args: str
    output: str
    required: tuple[str, ...] = ()
    protected: bool = False


@dataclass(frozen=True)
class Workload:
    id: str
    description: str
    steps: tuple[Step, ...]


@contextlib.contextmanager
def environment(**values: str):
    """Apply SUTRA env values for one mode and restore the previous process env."""
    saved = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            os.environ[name] = value
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _restore_pages(sutra, entry_id: str, page: int = 65_536) -> str | None:
    """Page through a stored original by byte range; None if any page fails.

    ``complete`` describes the *requested range*, not the whole payload, so the
    loop advances by the bytes actually returned until it reaches the recorded
    original size.
    """
    import json

    parts: list[str] = []
    start = 0
    size = None
    for _ in range(10_000):  # a bounded loop; 10k pages is 640 MiB
        response = sutra.recovery.restore(
            {"id": entry_id, "mode": "bytes", "start": start, "end": start + page,
             "max_bytes": page})
        payload = json.loads(response)
        if payload.get("status") != "ok":
            return None
        if size is None:
            size = int(payload.get("original_bytes") or 0)
        content = payload.get("content") or ""
        parts.append(content)
        consumed = len(content.encode("utf-8"))
        if consumed <= 0:
            return None
        start += consumed
        if start >= size:
            return "".join(parts)
        if not payload.get("more"):
            return None
    return None


def _clue_state(sutra, visible: str, clue: str, seen_ids: Iterable[str]) -> tuple[str, int]:
    """Return ``(state, restore_calls)`` for one required clue."""
    if clue in visible:
        return "visible", 0
    calls = 0
    for entry_id in dict.fromkeys(seen_ids):
        calls += 1
        import json
        payload = json.loads(sutra.recovery.restore(
            {"id": entry_id, "mode": "search", "query": clue}))
        if payload.get("status") == "ok" and clue in (payload.get("content") or ""):
            return "recoverable", calls
    return "lost", calls


def run_workload(workload: Workload, mode: str) -> dict[str, Any]:
    """Replay one workload in one mode and return its metrics."""
    sutra = load_plugin()
    sutra.reuse.reset()
    reuse_before = sutra.metrics_snapshot()["reuse"]
    if mode == "baseline":
        env = {"SUTRA_ENABLED": "0", "SUTRA_POLICY": "off", "SUTRA_REUSE": "0"}
    elif mode == "output":
        env = {"SUTRA_ENABLED": "1", "SUTRA_POLICY": "off", "SUTRA_REUSE": "0"}
    elif mode == "balanced":
        env = {"SUTRA_ENABLED": "1", "SUTRA_POLICY": "balanced", "SUTRA_REUSE": "1"}
    elif mode == "aggressive":
        env = {"SUTRA_ENABLED": "1", "SUTRA_POLICY": "aggressive", "SUTRA_REUSE": "1"}
    else:
        raise ValueError(f"unknown mode: {mode}")

    rows: list[dict[str, Any]] = []
    seen_ids: list[str] = []
    clues_total = clues_visible = clues_recoverable = clues_lost = 0
    restore_calls = 0
    protected_total = protected_ok = 0
    exact_restores = exact_failures = 0
    compression_ms = restore_ms = 0.0
    original_bytes = visible_bytes = 0
    reuse_hits = reuse_misses = 0

    # Marker ids are random in production by design. The harness substitutes a
    # deterministic sequence so two runs are byte-for-byte reproducible; this
    # changes no decision, only the quoted identifier.
    import itertools
    from unittest import mock

    counter = itertools.count()

    def fixed_token_hex(nbytes: int = 16) -> str:
        width = max(2, int(nbytes) * 2)
        return f"{next(counter):0{width}x}"[-width:]

    with tempfile.TemporaryDirectory(prefix="sutra-eval-") as home:
        with environment(HERMES_HOME=home, **env), \
                mock.patch.object(sutra._store.secrets, "token_hex", fixed_token_hex):
            cfg = sutra.configuration()
            section = sutra.policy.section(cfg) if mode in {"balanced", "aggressive"} else ""
            section_tokens = count_tokens(section) if section else 0
            cumulative = 0
            cumulative_tokens = 0
            input_tokens = 0
            for index, step in enumerate(workload.steps):
                started = time.perf_counter()
                if mode == "baseline":
                    outcome = sutra.Outcome(False, step.output, False, None,
                                            len(step.output.encode("utf-8")),
                                            len(step.output.encode("utf-8")))
                else:
                    outcome = sutra.process_text(
                        step.output, cfg=cfg, tool_name=step.tool,
                        session_id=f"{workload.id}-{mode}")
                compression_ms += (time.perf_counter() - started) * 1000
                visible = outcome.output
                original_bytes += outcome.original_bytes
                visible_bytes += outcome.compressed_bytes
                cumulative += outcome.compressed_bytes
                if outcome.id:
                    seen_ids.append(outcome.id)
                if outcome.id and "repeated output" in visible:
                    reuse_hits += 1
                elif mode != "baseline":
                    reuse_misses += 1
                visible_tokens = count_tokens(visible) or 0
                cumulative_tokens += visible_tokens
                # Every turn re-sends the tool results accumulated so far, plus
                # the policy section when one is active.
                turn_tokens = cumulative_tokens + (section_tokens or 0)
                input_tokens += turn_tokens
                step_clues = []
                # A clue must be recoverable from *this* step's retained original,
                # not from an unrelated earlier entry that happens to contain it.
                clue_ids = [outcome.id] if outcome.id else []
                for clue in step.required:
                    clues_total += 1
                    state, calls = _clue_state(sutra, visible, clue, clue_ids)
                    restore_calls += calls
                    step_clues.append({"clue": clue, "state": state})
                    if state == "visible":
                        clues_visible += 1
                    elif state == "recoverable":
                        clues_recoverable += 1
                    else:
                        clues_lost += 1
                rows.append({
                    "index": index,
                    "tool": step.tool,
                    "args": step.args,
                    "original_bytes": outcome.original_bytes,
                    "visible_bytes": outcome.compressed_bytes,
                    "visible_tokens": visible_tokens,
                    "turn_context_tokens": turn_tokens,
                    "changed": outcome.changed,
                    "id": outcome.id,
                    "reused": "repeated output" in visible,
                    "clues": step_clues,
                })
                if step.protected:
                    protected_total += 1
                    if visible == step.output:
                        protected_ok += 1
                if outcome.id and outcome.changed:
                    started = time.perf_counter()
                    restored = _restore_pages(sutra, outcome.id)
                    restore_ms += (time.perf_counter() - started) * 1000
                    exact_restores += 1
                    if restored != step.output:
                        exact_failures += 1
            final_context = cumulative_tokens + (section_tokens or 0)
            reuse_after = sutra.metrics_snapshot()["reuse"]
            reuse_state = {
                "hits": int(reuse_after.get("hits", 0)) - int(reuse_before.get("hits", 0)),
                "suppressed_bytes": (int(reuse_after.get("suppressed_bytes", 0))
                                     - int(reuse_before.get("suppressed_bytes", 0))),
            }

    extra_calls = len(workload.steps) - len(
        {(step.tool, step.args) for step in workload.steps})
    task_pass = clues_lost == 0 and protected_ok == protected_total and exact_failures == 0
    return {
        "mode": mode,
        "tool_calls": len(workload.steps),
        "repeated_tool_calls": extra_calls,
        "input_context_tokens": input_tokens,
        "final_context_tokens": final_context,
        "policy_tokens": section_tokens,
        "policy_overhead_tokens": (section_tokens or 0) * len(workload.steps),
        "original_bytes": original_bytes,
        "visible_bytes": visible_bytes,
        "compression_ms": round(compression_ms, 3),
        "restore_ms": round(restore_ms, 3),
        "restore_calls": restore_calls,
        "reuse_hits": reuse_hits,
        "reuse_misses": reuse_misses,
        "reuse_metric_hits": int(reuse_state.get("hits", 0)),
        "reuse_suppressed_bytes": int(reuse_state.get("suppressed_bytes", 0)),
        "quality": {
            "clues_total": clues_total,
            "visible": clues_visible,
            "recoverable": clues_recoverable,
            "lost": clues_lost,
            "protected_total": protected_total,
            "protected_ok": protected_ok,
            "exact_restores": exact_restores,
            "exact_restore_failures": exact_failures,
            "task_pass": task_pass,
        },
        "steps": rows,
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def environment_info() -> dict[str, Any]:
    sutra = load_plugin()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sutra_version": getattr(sutra, "__version__", None),
        "git_commit": _git_commit(),
        "tokenizer": tokenizer_note(),
        "model": "none (model-free replay; no provider call)",
    }


def compare(baseline: dict[str, Any], optimized: dict[str, Any]) -> dict[str, Any]:
    """Savings against the baseline, with the definitions written down."""
    base = baseline["input_context_tokens"]
    gross = optimized["input_context_tokens"]
    # The policy is an additional cost: charge it on top of the optimized
    # tool-result context rather than crediting it as a saving.
    net = gross + optimized["policy_overhead_tokens"]
    return {
        "definition": (
            "token savings = (baseline tool-result context tokens - optimized "
            "tool-result context tokens) / baseline * 100; net also charges the "
            "policy section on every turn"),
        "input_tokens": base,
        "optimized_tokens": gross,
        "policy_overhead_tokens": optimized["policy_overhead_tokens"],
        "net_tokens": net,
        "savings_pct": round(100 * (base - gross) / base, 2) if base else 0.0,
        "net_savings_pct": round(100 * (base - net) / base, 2) if base else 0.0,
    }


def run_all(workloads: Iterable[Workload],
            modes: Iterable[str] = ("baseline", "output", "balanced", "aggressive")
            ) -> dict[str, Any]:
    """Run every workload in every mode and aggregate the totals."""
    workloads = list(workloads)
    modes = list(modes)
    results = []
    totals: dict[str, dict[str, Any]] = {}
    for workload in workloads:
        by_mode = {}
        for mode in modes:
            by_mode[mode] = run_workload(workload, mode)
        entry = {
            "id": workload.id,
            "description": workload.description,
            "tool_calls": len(workload.steps),
            "repeated_tool_calls": len(workload.steps) - len(
                {(step.tool, step.args) for step in workload.steps}),
            "modes": by_mode,
        }
        if "baseline" in by_mode:
            entry["savings"] = {
                mode: compare(by_mode["baseline"], by_mode[mode])
                for mode in modes if mode != "baseline"
            }
        results.append(entry)

    for mode in modes:
        aggregate = {
            "workloads": len(results),
            "tool_calls": 0,
            "repeated_tool_calls": 0,
            "input_context_tokens": 0,
            "final_context_tokens": 0,
            "policy_tokens": 0,
            "policy_overhead_tokens": 0,
            "original_bytes": 0,
            "visible_bytes": 0,
            "compression_ms": 0.0,
            "restore_ms": 0.0,
            "restore_calls": 0,
            "reuse_hits": 0,
            "reuse_misses": 0,
            "reuse_metric_hits": 0,
            "reuse_suppressed_bytes": 0,
            "quality": {"clues_total": 0, "visible": 0, "recoverable": 0, "lost": 0,
                        "protected_total": 0, "protected_ok": 0,
                        "exact_restores": 0, "exact_restore_failures": 0,
                        "workloads_passed": 0},
        }
        for entry in results:
            row = entry["modes"][mode]
            for key in ("tool_calls", "repeated_tool_calls", "input_context_tokens",
                        "final_context_tokens", "policy_tokens",
                        "policy_overhead_tokens", "original_bytes", "visible_bytes",
                        "restore_calls", "reuse_hits", "reuse_misses",
                        "reuse_metric_hits", "reuse_suppressed_bytes"):
                aggregate[key] += row[key]
            for key in ("compression_ms", "restore_ms"):
                aggregate[key] += row[key]
            for key in ("clues_total", "visible", "recoverable", "lost",
                        "protected_total", "protected_ok", "exact_restores",
                        "exact_restore_failures"):
                aggregate["quality"][key] += row["quality"][key]
            aggregate["quality"]["workloads_passed"] += int(row["quality"]["task_pass"])
        aggregate["compression_ms"] = round(aggregate["compression_ms"], 3)
        aggregate["restore_ms"] = round(aggregate["restore_ms"], 3)
        totals[mode] = aggregate

    summary: dict[str, Any] = {
        "report": "phase2-hermes-cost",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": environment_info(),
        "definitions": {
            "deterministic_ids": (
                "marker ids are a deterministic sequence in this harness; production "
                "ids are random, so real runs vary by a few tokens per rewrite"),
            "input_context_tokens": (
                "sum over turns of every visible tool-result token so far; the "
                "system prompt and user text are excluded because they are "
                "identical across modes and are not what SUTRA controls"),
            "tool_calls": "fixed by the recorded workload; identical across modes",
            "cost": "unavailable: no provider call, no authoritative usage or price",
            "policy_behaviour": (
                "not modelled: the same tool sequence is replayed in every mode"),
        },
        "workloads": results,
        "totals": totals,
    }
    if "baseline" in totals:
        summary["savings"] = {
            mode: compare(totals["baseline"], totals[mode])
            for mode in modes if mode != "baseline"
        }
    return summary


def format_table(summary: dict[str, Any]) -> str:
    """A compact Markdown table for the console and the report."""
    lines = [
        "| Mode | Context tokens | Net tokens | Savings | Net savings | Clues kept | Task |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for mode, row in summary["totals"].items():
        savings = summary.get("savings", {}).get(mode)
        kept = (row["quality"]["visible"] + row["quality"]["recoverable"])
        total = row["quality"]["clues_total"]
        task = f"{row['quality']['workloads_passed']}/{row['workloads']}"
        net = savings["net_tokens"] if savings else row["input_context_tokens"]
        saved = f"{savings['savings_pct']}%" if savings else "—"
        net_saved = f"{savings['net_savings_pct']}%" if savings else "—"
        lines.append(
            f"| {mode} | {row['input_context_tokens']:,} | {net:,} | {saved} | "
            f"{net_saved} | {kept}/{total} | {task} |")
    return "\n".join(lines)


__all__ = [
    "ROOT",
    "Step",
    "Workload",
    "compare",
    "count_tokens",
    "environment",
    "environment_info",
    "format_table",
    "load_plugin",
    "run_all",
    "run_workload",
    "tokenizer_note",
]
