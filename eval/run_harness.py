"""SUTRA Phase 1 eval harness: three conditions (A / B / C).

Replays every task in ``eval/tasks/`` through the real SUTRA seam under
three conditions and reports raw results, per-task aggregates, condition
comparisons, variability, and evidence levels:

* Condition A (control)        -- no compression, no recovery. Raw output only.
* Condition B (compression+recovery) -- compress, retain, recover, verify.
* Condition C (full optimization)    -- B plus exact-duplicate reuse and
  the policy engine.

Usage:
    python eval/run_harness.py run               # all tasks, 3 reps each
    python eval/run_harness.py run --task eval-001 --reps 4
    python eval/run_harness.py compare           # latest run under eval/results
    python eval/run_harness.py compare --run eval/results/run-2026...

The same interface is exposed as ``sutra eval run`` / ``sutra eval compare``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sutra
from sutra import config as _config
from sutra.core import Outcome, process_text
from sutra import metrics, recovery, reuse, store as _store

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional humanization, consistent with the rest of the sutra CLI. Falls back
# to plain text when the UI module is unavailable or output is not a TTY.
try:
    from sutra.ui import colorize as _ui_colorize, icon as _ui_icon  # type: ignore
except Exception:  # pragma: no cover - sutra.ui optional in this harness
    _ui_colorize = None
    _ui_icon = None


def _colorize(text: str, style: str = "") -> str:
    if _ui_colorize is None:
        return text
    try:
        return _ui_colorize(text, style)
    except Exception:
        return text


def _icon(status: str) -> str:
    if _ui_icon is None:
        return ""
    try:
        return _ui_icon(status) or ""
    except Exception:
        return ""


OK = _icon("ok") or "✓"
FAIL = _icon("fail") or "✗"
WARN = _icon("warn") or "→"

DEFAULT_REPS = 3
MAX_REPS = 5
RESULTS_DIR = ROOT / "eval" / "results"
TASKS_DIR = ROOT / "eval" / "tasks"
PAGE_BYTES = 65_536  # equals HARD_RECOVERY_MAX_BYTES; one restore page per call

CONDITIONS: dict[str, dict[str, Any]] = {
    "A": {
        "label": "control",
        "description": "No compression, no recovery. Raw output only.",
        "env": {
            "SUTRA_ENABLED": "0",
            "SUTRA_POLICY": "off",
            "SUTRA_REUSE": "0",
            "SUTRA_STORE": "0",
        },
    },
    "B": {
        "label": "compression+recovery",
        "description": "Compress output, recover on demand, verify integrity.",
        "env": {
            "SUTRA_ENABLED": "1",
            "SUTRA_POLICY": "off",
            "SUTRA_REUSE": "0",
            "SUTRA_STORE": "1",
        },
    },
    "C": {
        "label": "full optimization",
        "description": "Compression + recovery + reuse (exact-duplicate cache) + policy engine.",
        "env": {
            "SUTRA_ENABLED": "1",
            "SUTRA_POLICY": "balanced",
            "SUTRA_REUSE": "1",
            "SUTRA_STORE": "1",
        },
    },
}

CFG_FIELDS = (
    "max_bytes", "head_bytes", "tail_bytes", "store_enabled",
    "store_max_entry_bytes", "policy_mode", "reuse", "reuse_min_bytes",
    "enabled", "store_dir",
)


@contextmanager
def condition_env(env: dict[str, str]):
    """Apply SUTRA env values for one condition and restore the process env."""
    saved = {name: os.environ.get(name) for name in env}
    try:
        for name, value in env.items():
            os.environ[name] = value
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# --- scenario steps ---------------------------------------------------------

@contextmanager
def _timed() -> Any:
    started = time.perf_counter()
    yield lambda: (time.perf_counter() - started) * 1000.0


def _step_record(index: int, step: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "tool": step.get("tool", "generic"),
        "args": dict(step.get("args", {})),
        "original_bytes": 0,
        "visible_bytes": 0,
        "changed": False,
        "marker": False,
        "id": None,
        "compressed_bytes": 0,
        "reused": False,
        "clues": [],
        "compression_ms": 0.0,
        "restore_ms": 0.0,
        "restore_ok": True,
        "restore_bytes": 0,
        "error": None,
    }


def _run_store_step(
    step: dict[str, Any], store: _store.Store, session: str,
    last_put: list[str | None], earlier_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute one direct store operation (put / corrupt_meta / prune / get)."""
    op = str(step.get("args", {}).get("op", "put"))
    record: dict[str, Any] = {"store_op": op}
    with _timed() as elapsed:
        if op == "put":
            text = step.get("args", {}).get("text", step.get("output", ""))
            entry = store.put(text, tool_name=step.get("tool", "sutra_store"),
                              session_id=session)
            last_put[0] = entry.id if entry else None
            record.update({
                "original_bytes": len(text.encode("utf-8", "replace")),
                "visible_bytes": len(text.encode("utf-8", "replace")),
                "id": last_put[0],
                "put_ok": entry is not None,
            })
        elif op == "corrupt_meta":
            target = last_put[0]
            ok = False
            if target:
                try:
                    with open(store.meta_path(target), "w", encoding="utf-8") as fh:
                        fh.write("{not valid json")
                    ok = True
                except OSError:
                    ok = False
            record.update({"corrupt_ok": ok, "target": target})
        elif op == "prune":
            record["prune_report"] = store.prune(now=time.time())
        elif op == "get":
            args = step.get("args", {})
            target = _resolve_placeholders(args.get("id") or last_put[0],
                                           step, earlier_steps)
            lookup = store.get(target) if target else None
            record.update({
                "target": target,
                "get_status": lookup.status if lookup else None,
                "get_entry": lookup.entry is not None if lookup else None,
            })
        else:
            record["error"] = f"unknown store op: {op}"
    record["restore_ms"] = elapsed()
    return record


def _resolve_placeholders(value: Any, step: dict[str, Any],
                          earlier_steps: list[dict[str, Any]]) -> Any:
    """Resolve ``{<step_index>.<field>}`` refs to earlier step results."""
    if not isinstance(value, str):
        return value
    import re
    match = re.fullmatch(r"\{(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\}", value.strip())
    if not match:
        return value
    index = int(match.group(1))
    field = match.group(2)
    if index < 0 or index >= len(earlier_steps):
        return value
    return earlier_steps[index].get(field)


def _clue_state(
    sutra_module: Any, store: _store.Store, visible: str, clue: str,
    seen_ids: list[str],
) -> tuple[str, int, float]:
    """Return ``(state, restore_calls, restore_ms)`` for one required clue."""
    if clue in visible:
        return "visible", 0, 0.0
    calls = 0
    total_ms = 0.0
    for entry_id in dict.fromkeys(seen_ids):
        calls += 1
        with _timed() as elapsed:
            response = json.loads(
                sutra_module.recovery.restore(
                    {"id": entry_id, "mode": "search", "query": clue}, store=store))
        total_ms += elapsed()
        if response.get("status") == "ok" and clue in (response.get("content") or ""):
            return "recoverable", calls, total_ms
    return "lost", calls, total_ms


def _restore_full(store: _store.Store, entry_id: str, original_bytes: int) -> str | None:
    """Page through a stored original; None if any page fails."""
    parts: list[str] = []
    start = 0
    while start < original_bytes:
        end = min(start + PAGE_BYTES, original_bytes)
        response = json.loads(recovery.restore(
            {"id": entry_id, "mode": "bytes", "start": start, "end": end,
             "max_bytes": PAGE_BYTES}, store=store))
        if response.get("status") != "ok":
            return None
        content = response.get("content") or ""
        parts.append(content)
        consumed = len(content.encode("utf-8"))
        if consumed <= 0:
            return None
        start += consumed
    return "".join(parts)


def _run_rep(
    task: dict[str, Any], condition: str, rep: int, home: str,
    cfg: Any, cfg_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Replay one task once under one condition."""
    reuse.reset()
    store = _store.Store(_store.StoreConfig.from_config(cfg), cfg)
    session = f"{task['task_id']}-{condition}-rep{rep}"
    seen_ids: list[str] = []
    last_put: list[str | None] = [None]
    earlier_steps: list[dict[str, Any]] = []

    metrics_before = metrics.snapshot(store)
    reuse_hits_before = metrics_before["reuse"]["hits"]
    steps_records: list[dict[str, Any]] = []
    restore_calls = 0
    restore_ms_total = 0.0
    compression_ms_total = 0.0
    original_bytes_total = 0
    visible_bytes_total = 0
    clues_total = clues_visible = clues_recoverable = clues_lost = 0
    protected_total = protected_ok = 0
    exact_restores = exact_failures = 0
    store_puts = store_gets = 0

    for index, step in enumerate(task["scenario"]()):
        step_record = _step_record(index, step)
        step_record["_earlier_steps"] = earlier_steps  # placeholder resolution
        try:
            if step.get("tool") == "sutra_store":
                store_puts += 1
                step_record.update(_run_store_step(step, store, session,
                                                   last_put, earlier_steps))
                restore_ms_total += step_record["restore_ms"]
                if step_record.get("id"):
                    last_put[0] = step_record["id"]
                    seen_ids.append(step_record["id"])
                if step_record.get("store_op") == "get":
                    store_gets += 1
            else:
                text = step.get("output", "")
                original_bytes = len(text.encode("utf-8", "replace"))
                step_compression_ms = 0.0
                if condition == "A":
                    outcome = Outcome(
                        False, text, False, None, original_bytes, original_bytes)
                else:
                    with _timed() as _compression_timer:
                        outcome = process_text(
                            text, cfg=cfg, tool_name=step.get("tool", "generic"),
                            session_id=session)
                    step_compression_ms = _compression_timer()
                compression_ms_total += step_compression_ms
                visible = outcome.output
                visible_bytes = len(visible.encode("utf-8", "replace"))
                step_record.update({
                    "original_bytes": original_bytes,
                    "visible_bytes": visible_bytes,
                    "changed": bool(outcome.changed),
                    "marker": bool(outcome.marker),
                    "id": outcome.id,
                    "compressed_bytes": outcome.compressed_bytes,
                    "compression_ms": round(step_compression_ms, 4),
                })
                if outcome.id:
                    seen_ids.append(outcome.id)

                for clue in step.get("required", ()) or ():
                    state, calls, ms = _clue_state(
                        sutra, store, visible, clue, seen_ids)
                    step_record["clues"].append(
                        {"clue": clue, "state": state, "restore_calls": calls})
                    restore_calls += calls
                    restore_ms_total += ms
                    clues_total += 1
                    if state == "visible":
                        clues_visible += 1
                    elif state == "recoverable":
                        clues_recoverable += 1
                    else:
                        clues_lost += 1

                if outcome.id and outcome.changed:
                    with _timed() as restore_timer:
                        restored = _restore_full(store, outcome.id, original_bytes)
                    step_restore_ms = restore_timer()
                    restore_calls += 1
                    restore_ms_total += step_restore_ms
                    step_record["restore_ms"] = round(step_restore_ms, 4)
                    if restored is None:
                        step_record["restore_ok"] = False
                        exact_failures += 1
                    else:
                        step_record["restore_bytes"] = len(
                            restored.encode("utf-8", "replace"))
                        step_record["restore_ok"] = restored == text
                        if restored == text:
                            exact_restores += 1
                        else:
                            exact_failures += 1

                if step.get("protected"):
                    protected_total += 1
                    if visible == text:
                        protected_ok += 1

            hits_now = metrics.snapshot(store)["reuse"]["hits"]
            step_record["reused"] = hits_now > reuse_hits_before
            reuse_hits_before = hits_now
        except Exception as exc:  # one failing step never aborts a rep
            step_record["error"] = f"{type(exc).__name__}: {exc}"
        step_record.pop("_earlier_steps", None)
        steps_records.append(step_record)
        earlier_steps.append(step_record)

    # per-rep reuse hits: counter is cumulative, diff against rep start
    reuse_hits = metrics.snapshot(store)["reuse"]["hits"] - metrics_before["reuse"]["hits"]

    original_bytes_total = sum(s["original_bytes"] for s in steps_records)
    visible_bytes_total = sum(s["visible_bytes"] for s in steps_records)

    checks: dict[str, bool] = {
        "protected_preserved": protected_ok == protected_total,
        "exact_restore_ok": exact_failures == 0,
        "clues_not_lost": clues_lost == 0,
    }
    if callable(task.get("verify")):
        try:
            task_checks = task["verify"]({
                "rep": rep, "condition": condition, "cfg": cfg_snapshot,
                "steps": steps_records, "metrics": {
                    "original_bytes": original_bytes_total,
                    "visible_bytes": visible_bytes_total,
                },
            })
            for name, value in task_checks.items():
                checks[f"{task['task_id']}.{name}"] = bool(value)
        except Exception as exc:
            checks[f"{task['task_id']}.verify_error"] = False
            steps_records[-1]["error"] = steps_records[-1]["error"] or (
                f"task.verify raised {type(exc).__name__}: {exc}")

    passed = all(checks.values())
    return {
        "rep": rep,
        "condition": condition,
        "env": {name: os.environ.get(name) for name in
                (_config_env_names(condition))},
        "cfg": cfg_snapshot,
        "session": session,
        "steps": steps_records,
        "metrics": {
            "original_bytes": original_bytes_total,
            "visible_bytes": visible_bytes_total,
            "compression_ms": round(compression_ms_total, 4),
            "restore_ms": round(restore_ms_total, 4),
            "restore_calls": restore_calls,
            "clues_total": clues_total,
            "clues_visible": clues_visible,
            "clues_recoverable": clues_recoverable,
            "clues_lost": clues_lost,
            "protected_total": protected_total,
            "protected_ok": protected_ok,
            "exact_restores": exact_restores,
            "exact_failures": exact_failures,
            "reuse_hits": reuse_hits,
            "store_puts": store_puts,
            "store_gets": store_gets,
        },
        "checks": checks,
        "passed": passed,
    }


def _config_env_names(condition: str) -> tuple[str, ...]:
    return ("SUTRA_ENABLED", "SUTRA_MAX_BYTES", "SUTRA_HEAD_BYTES",
            "SUTRA_TAIL_BYTES", "SUTRA_STORE", "SUTRA_STORE_DIR",
            "SUTRA_POLICY", "SUTRA_REUSE", "SUTRA_REUSE_MIN_BYTES",
            "SUTRA_RECOVERY_MAX_BYTES", "SUTRA_RECOVERY_MAX_MATCHES",
            "HERMES_HOME")


# --- aggregation -----------------------------------------------------------

def _stats(values: list[float]) -> dict[str, float]:
    values = [float(v) for v in values]
    if not values:
        return {"mean": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0,
                "cv": 0.0, "count": 0}
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {"mean": mean, "stdev": stdev, "min": min(values), "max": max(values),
            "cv": (stdev / mean) if mean else 0.0, "count": len(values)}


def _aggregate(rep_results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = len(rep_results) > 0 and all(r["passed"] for r in rep_results)
    checks: dict[str, Any] = {}
    for rep in rep_results:
        for name, value in rep["checks"].items():
            entry = checks.setdefault(name, {"passed": 0, "total": 0})
            entry["total"] += 1
            if value:
                entry["passed"] += 1
    return {
        "reps": len(rep_results),
        "original_bytes": _stats([r["metrics"]["original_bytes"] for r in rep_results]),
        "visible_bytes": _stats([r["metrics"]["visible_bytes"] for r in rep_results]),
        "compression_ms": _stats([r["metrics"]["compression_ms"] for r in rep_results]),
        "restore_ms": _stats([r["metrics"]["restore_ms"] for r in rep_results]),
        "restore_calls": sum(r["metrics"]["restore_calls"] for r in rep_results),
        "clues_total": sum(r["metrics"]["clues_total"] for r in rep_results),
        "clues_visible": sum(r["metrics"]["clues_visible"] for r in rep_results),
        "clues_recoverable": sum(r["metrics"]["clues_recoverable"] for r in rep_results),
        "clues_lost": sum(r["metrics"]["clues_lost"] for r in rep_results),
        "exact_restores": sum(r["metrics"]["exact_restores"] for r in rep_results),
        "exact_failures": sum(r["metrics"]["exact_failures"] for r in rep_results),
        "protected_total": sum(r["metrics"]["protected_total"] for r in rep_results),
        "protected_ok": sum(r["metrics"]["protected_ok"] for r in rep_results),
        "reuse_hits": sum(r["metrics"]["reuse_hits"] for r in rep_results),
        "pass_rate": {
            "rate": (sum(1 for r in rep_results if r["passed"]) / len(rep_results)
                     if rep_results else 0.0),
            "passed": sum(1 for r in rep_results if r["passed"]),
            "total": len(rep_results),
        },
        "checks": checks,
        "passed": passed,
    }


def _run_condition(task: dict[str, Any], condition: str, reps: int,
                   home: str) -> dict[str, Any]:
    env = dict(CONDITIONS[condition]["env"])
    env.update(task.get("env_overrides", {}))
    rep_results: list[dict[str, Any]] = []
    with condition_env(env):
        cfg = _config.resolve()
        cfg_snapshot = {name: getattr(cfg, name) for name in CFG_FIELDS}
        for rep in range(1, reps + 1):
            rep_results.append(
                _run_rep(task, condition, rep, home, cfg, cfg_snapshot))
    aggregate = _aggregate(rep_results)
    return {
        "condition": condition,
        "label": CONDITIONS[condition]["label"],
        "description": CONDITIONS[condition]["description"],
        "env": env,
        "cfg": cfg_snapshot,
        "reps": rep_results,
        "aggregate": aggregate,
        "passed": aggregate["passed"],
    }


def _evidence_achieved(
    cond_results: dict[str, dict[str, Any]], conditions: list[str],
) -> str:
    passed = [c for c in conditions if cond_results[c]["passed"]]
    any_rep_passed = any(
        r["passed"] for cr in cond_results.values() for r in cr["reps"])
    if conditions and len(passed) == len(conditions):
        return "PRODUCTION VERIFIED"
    if ({"B", "C"} <= set(conditions)
            and cond_results.get("B", {}).get("passed")
            and cond_results.get("C", {}).get("passed")):
        return "EXPERIMENTALLY DEMONSTRATED"
    if any_rep_passed:
        return "TESTED"
    return "IMPLEMENTED"


def _variability(
    cond_results: dict[str, dict[str, Any]], conditions: list[str],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric in ("visible_bytes", "compression_ms"):
        per_cond: dict[str, Any] = {}
        high = False
        for c in conditions:
            agg = cond_results[c]["aggregate"][metric]
            per_cond[c] = {"mean": agg["mean"], "stdev": agg["stdev"],
                           "cv": agg["cv"]}
            if agg["cv"] > 0.25 and agg["mean"] >= 1.0:
                high = True
        out[metric] = {"per_condition": per_cond, "high_variability": high}
    for metric in ("clues_lost", "exact_failures"):
        out[metric] = {c: cond_results[c]["aggregate"][metric] for c in conditions}
    out["pass_rate"] = {
        c: cond_results[c]["aggregate"]["pass_rate"]["rate"] for c in conditions
    }
    return out


def run_task(
    task: dict[str, Any], conditions: list[str], reps: int, home: str,
) -> dict[str, Any]:
    cond_results = {
        condition: _run_condition(task, condition, reps, home)
        for condition in conditions
    }
    all_passed = all(cr["passed"] for cr in cond_results.values())
    total_reps = sum(len(cr["reps"]) for cr in cond_results.values())
    passed_reps = sum(
        1 for cr in cond_results.values() for r in cr["reps"] if r["passed"])
    return {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "description": task["description"],
        "difficulty_rating": task["difficulty_rating"],
        "classification": task["classification"],
        "human_review": task["human_review"],
        "acceptance_criteria": task["acceptance_criteria"],
        "expected_behavior": task["expected_behavior"],
        "evidence_level": task["evidence_level"],
        "evidence_achieved": _evidence_achieved(cond_results, conditions),
        "conditions": cond_results,
        "variability": _variability(cond_results, conditions),
        "passed": all_passed,
        "reps": {"total": total_reps, "passed": passed_reps},
    }


# --- task discovery ---------------------------------------------------------

def load_tasks() -> list[dict[str, Any]]:
    """Import every ``eval/tasks/eval-*.py`` module as a runnable task."""
    tasks: list[dict[str, Any]] = []
    if not TASKS_DIR.is_dir():
        return tasks
    for path in sorted(TASKS_DIR.glob("eval-*.py")):
        module_name = f"eval_harness_task_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # a broken task file must not kill the harness
            tasks.append({
                "task_id": path.stem, "path": str(path), "runnable": False,
                "error": f"failed to load: {type(exc).__name__}: {exc}",
            })
            continue
        scenario = getattr(module, "scenario", None)
        tasks.append({
            "task_id": getattr(module, "task_id", path.stem),
            "task_type": getattr(module, "task_type", "standard"),
            "initial_snapshot": getattr(module, "initial_snapshot", ""),
            "description": getattr(module, "description", ""),
            "acceptance_criteria": list(getattr(module, "acceptance_criteria", [])),
            "expected_behavior": dict(getattr(module, "expected_behavior", {})),
            "validation_scripts": list(getattr(module, "validation_scripts", [])),
            "human_review": dict(getattr(module, "human_review", {})),
            "difficulty_rating": str(getattr(module, "difficulty_rating", "medium")),
            "classification": dict(getattr(module, "classification", {})),
            "evidence_level": str(getattr(module, "evidence_level", "IMPLEMENTED")),
            "status": str(getattr(module, "status", "pending")),
            "scenario": scenario if callable(scenario) else None,
            "verify": getattr(module, "verify", None),
            "env_overrides": dict(getattr(module, "env_overrides", {})),
            "repeated_steps": bool(getattr(module, "repeated_steps", False)),
            "path": str(path),
            "runnable": callable(scenario),
        })
    return tasks


# --- run orchestration ------------------------------------------------------

def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or None
    except Exception:
        return None


def run_all(
    task_ids: list[str] | None = None,
    conditions: list[str] | None = None,
    reps: int = DEFAULT_REPS,
    out_dir: str | os.PathLike[str] = RESULTS_DIR,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Run every selected task under every condition; write raw results."""
    conditions = [c for c in (conditions or ["A", "B", "C"]) if c in CONDITIONS]
    if not conditions:
        conditions = ["A", "B", "C"]
    tasks = [t for t in load_tasks()
             if t.get("runnable") and (not task_ids or t["task_id"] in task_ids)]
    if task_ids:
        missing = [tid for tid in task_ids
                   if tid not in {t["task_id"] for t in tasks}]
        if missing:
            raise ValueError(f"unknown task ids: {', '.join(missing)}")
    run_id = time.strftime("run-%Y%m%dT%H%M%S")
    run_dir = Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    task_results: list[dict[str, Any]] = []
    for task in tasks:
        with tempfile.TemporaryDirectory(prefix=f"sutra-eval-{task['task_id']}-") as home:
            result = run_task(task, conditions, reps, home)
            task_results.append(result)
            for condition in conditions:
                path = run_dir / f"raw-{task['task_id']}-{condition}.json"
                path.write_text(
                    json.dumps(result["conditions"][condition], indent=2) + "\n")

    summary: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sutra_version": getattr(sutra, "__version__", "unknown"),
        "git_commit": _git_commit(),
        "python": sys.version,
        "reps": reps,
        "conditions": {
            condition: {
                "label": CONDITIONS[condition]["label"],
                "description": CONDITIONS[condition]["description"],
                "env": CONDITIONS[condition]["env"],
            }
            for condition in conditions
        },
        "tasks": task_results,
        "passed": all(t["passed"] for t in task_results),
        "total_reps": sum(t["reps"]["total"] for t in task_results),
        "passed_reps": sum(t["reps"]["passed"] for t in task_results),
    }
    try:
        from .compare import compute_comparison  # type: ignore
    except ImportError:  # run as a script: eval/run_harness.py
        from compare import compute_comparison  # type: ignore
    comparison = compute_comparison(summary)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (run_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n")
    return summary, comparison, run_dir


# --- humanized output -------------------------------------------------------

def print_run_report(summary: dict[str, Any]) -> None:
    print(_colorize("SUTRA eval — Phase 1 harness (three conditions A/B/C)", "heading"))
    print(_colorize("-" * 70, "muted"))
    print(f"run: {summary['run_id']}   sutra: {summary['sutra_version']}   "
          f"reps per task/condition: {summary['reps']}")
    conds = summary["conditions"]
    print("conditions: " + "   |   ".join(
        f"{c} {info['label']}" for c, info in conds.items()))
    print("")
    header = f"{'task':<10}" + "".join(f"  {c:^12}" for c in conds) + "      evidence"
    print(header)
    print("-" * len(header))
    for task in summary["tasks"]:
        cells = []
        for c in conds:
            cr = task["conditions"].get(c, {})
            passed = bool(cr.get("passed"))
            cells.append(f"{OK if passed else FAIL} {'pass' if passed else 'FAIL':<6}")
        evidence = f"{task['evidence_level']} → {task['evidence_achieved']}"
        print(f"{task['task_id']:<10}" + "".join(cells) + f"      {evidence}")
    print("")
    print(f"result: {'all tasks passed' if summary['passed'] else 'failures detected'}  "
          f"{summary['passed_reps']}/{summary['total_reps']} reps passed  "
          f"({len(summary['tasks'])} tasks, {len(conds)} conditions)")
    for task in summary["tasks"]:
        var = task.get("variability", {})
        for metric, info in var.items():
            if isinstance(info, dict) and info.get("high_variability"):
                print(f"{WARN} {task['task_id']}: {metric} shows high variability "
                      f"(per-rep cv above 0.25 in at least one condition); "
                      f"consider --reps 5")
    print("")


# --- CLI --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sutra eval",
        description="SUTRA Phase 1 eval harness: three conditions (A control / "
                    "B compression+recovery / C full optimization).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run tasks across all conditions.")
    p_run.add_argument("--task", default=None, help="Run only this task id.")
    p_run.add_argument("--conditions", default="ABC",
                       help="Conditions to run, e.g. AB or ABC (default: ABC).")
    p_run.add_argument("--reps", type=int, default=DEFAULT_REPS,
                       help=f"Reps per task per condition (1-{MAX_REPS}).")
    p_run.add_argument("--out", default=str(RESULTS_DIR),
                       help="Results directory (default: eval/results).")
    p_run.add_argument("--json", action="store_true",
                       help="Emit machine-readable JSON instead of text.")

    p_comp = sub.add_parser("compare", help="Compare conditions from a run.")
    p_comp.add_argument("--run", default=None,
                        help="Run directory (default: latest under --out).")
    p_comp.add_argument("--out", default=str(RESULTS_DIR),
                        help="Results directory (default: eval/results).")
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    reps = max(1, min(MAX_REPS, args.reps if args.reps else DEFAULT_REPS))
    conds = [c for c in args.conditions.upper() if c in CONDITIONS] or ["A", "B", "C"]
    tasks = [t for t in load_tasks() if t.get("runnable")
             and (not args.task or t["task_id"] == args.task)]
    if args.task and not tasks:
        print(_colorize(f"error: unknown or unrunnable task id: {args.task}", "error")
              or f"error: unknown or unrunnable task id: {args.task}")
        return 2
    summary, comparison, run_dir = run_all(
        task_ids=[t["task_id"] for t in tasks], conditions=conds, reps=reps,
        out_dir=args.out)
    if args.json:
        print(json.dumps({"summary": summary, "comparison": comparison}, indent=2))
    else:
        print_run_report(summary)
        try:
            from .compare import format_comparison  # type: ignore
        except ImportError:  # run as a script: eval/run_harness.py
            from compare import format_comparison  # type: ignore
        print(format_comparison(comparison, tty=sys.stdout.isatty()))
    return 0 if summary["passed"] else 1


def _latest_run_dir(out_dir: str | os.PathLike[str]) -> Path | None:
    base = Path(out_dir)
    if not base.is_dir():
        return None
    runs = sorted((p for p in base.iterdir()
                   if p.is_dir() and p.name.startswith("run-")),
                  key=lambda p: p.name)
    return runs[-1] if runs else None


def _cmd_compare(args: argparse.Namespace) -> int:
    run_dir = Path(args.run) if args.run else _latest_run_dir(args.out)
    if not run_dir or not (run_dir / "summary.json").exists():
        print(f"error: no run summary found in {args.out}")
        return 2
    summary = json.loads((run_dir / "summary.json").read_text())
    try:
        from .compare import compute_comparison, format_comparison  # type: ignore
    except ImportError:  # run as a script: eval/run_harness.py
        from compare import compute_comparison, format_comparison  # type: ignore
    comparison = compute_comparison(summary)
    cmp_path = run_dir / "comparison.json"
    if not cmp_path.exists():
        cmp_path.write_text(json.dumps(comparison, indent=2) + "\n")
    print(format_comparison(comparison, tty=sys.stdout.isatty()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "compare":
        return _cmd_compare(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
