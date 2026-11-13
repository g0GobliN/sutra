"""eval/tasks/eval-004.py — Refactor: typed CompressionStats contract.

Task ID: eval-004
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from typing import Any

# --- Task identification ---
task_id = "eval-004"
task_type = "refactor"
initial_snapshot = "git:tooltrim@HEAD"

# --- Task description ---
description = (
    "Compression results carry a typed record (CompressionStats) that must be "
    "readable both as attributes (stats.rule) and as a mapping "
    "(stats[\"rule\"]), with as_dict() for telemetry. The seam's CompressionStats "
    "and the engine's must be the same type so no parallel record drifts."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "CompressionStats supports attribute access: stats.rule",
    "CompressionStats supports item access: stats[\"rule\"]",
    "as_dict() returns a plain dict for telemetry export",
    "sutra.core.CompressionStats is sutra.engine.CompressionStats (one type)",
    "Outcome records carry id, original_bytes, and compressed_bytes fields",
]

# --- Expected behavior ---
expected_behavior = {
    "stats_rule": "none",
    "stats_dual_access": True,
    "core_is_engine_stats": True,
}

validation_scripts = [
    {
        "name": "stats_contract",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-004 --reps 3",
        "description": "Compress one small result under A/B/C and assert the stats contract holds under every condition.",
        "expected_pass": True,
    },
]

human_review = {
    "instructions": (
        "1. Confirm one CompressionStats type is shared by core and engine.\n"
        "2. Confirm attribute, item, and as_dict() reads agree.\n"
        "3. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex."
    ),
    "difficulty_rating": "Moderate",
    "estimated_time_minutes": 20,
}

difficulty_rating = "Moderate"

classification = {
    "type": "standard",
    "reason": "API-shape inspection; deterministic across conditions.",
    "adversarial_note": None,
}

evidence_level = "IMPLEMENTED"
status = "pending"

# --- Harness scenario ---

SCENARIO_STEPS = [
    {
        "tool": "generic",
        "args": {},
        "output": "config-driven stats contract probe text\n",
        "required": (),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    from sutra import config as _config
    from sutra import engine as _engine
    from sutra.core import CompressionStats as _core_stats

    cfg = _config.resolve()
    out, stats = _engine.compress(
        "config-driven stats contract probe text\n", cfg=cfg)

    checks: dict[str, bool] = {
        "eval-004-stats-rule-access": stats.rule == "none",
        "eval-004-stats-item-access": stats["rule"] == "none",
        "eval-004-stats-as-dict": isinstance(stats.as_dict(), dict),
        "eval-004-core-stats-is-engine-stats": (
            _core_stats is _engine.CompressionStats),
    }
    if rep.get("steps"):
        step = rep["steps"][0]
        checks["eval-004-outcome-has-id-field"] = "id" in step
        checks["eval-004-outcome-has-bytes-fields"] = (
            "original_bytes" in step and "visible_bytes" in step)
    return checks


env_overrides = {}
repeated_steps = False
