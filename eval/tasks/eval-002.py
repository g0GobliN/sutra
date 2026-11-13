"""eval/tasks/eval-002.py — Feature: configuration defaults stable across conditions.

Task ID: eval-002
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from typing import Any

# --- Task identification ---
task_id = "eval-002"
task_type = "feature"
initial_snapshot = "git:tooltrim@HEAD"

# --- Task description ---
description = (
    "SUTRA's resolved configuration must stay stable and correct under every "
    "condition: byte budgets, the reuse floor, store retention, and policy "
    "mode are read from the environment and applied consistently. No field "
    "may drift between the control condition and the optimized conditions."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "SUTRA_MAX_BYTES defaults to 24_576 bytes under every condition",
    "SUTRA_REUSE_MIN_BYTES defaults to 4096 bytes under every condition",
    "the store is disabled under condition A and enabled under B and C",
    "compression is disabled under condition A and enabled under B and C",
    "the policy mode is off under A and B, balanced under C",
]

# --- Expected behavior ---
expected_behavior = {
    "max_bytes": 24576,
    "reuse_min_bytes": 4096,
    "policy": {"A": "off", "B": "off", "C": "balanced"},
    "store": {"A": False, "B": True, "C": True},
    "enabled": {"A": False, "B": True, "C": True},
}

# --- Validation scripts (automated where possible) ---
validation_scripts = [
    {
        "name": "config_defaults",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-002",
        "description": "Run eval-002 under all three conditions and assert the resolved config matches the condition's contract.",
        "expected_pass": True,
    },
]

# --- Human review instructions ---
human_review = {
    "instructions": (
        "1. Confirm config.resolve() reflects SUTRA_ENABLED/SUTRA_STORE/"
        "SUTRA_POLICY/SUTRA_REUSE under each condition.\n"
        "2. Confirm defaults: max_bytes=24_576, reuse_min_bytes=4096.\n"
        "3. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex.\n"
        "4. Note any field that differs between conditions unexpectedly."
    ),
    "difficulty_rating": "Easy",
    "estimated_time_minutes": 15,
}

# --- Expected difficulty rating ---
difficulty_rating = "Easy"  # Trivial | Easy | Moderate | Hard | Complex

# --- Adversarial vs standard classification ---
classification = {
    "type": "standard",
    "reason": "Read-only configuration inspection under three environments; "
              "no adversarial inputs.",
    "adversarial_note": None,
}

# --- Evidence tracking ---
evidence_level = "IMPLEMENTED"  # IMPLEMENTED | TESTED | EXPERIMENTALLY DEMONSTRATED | PRODUCTION VERIFIED

# --- Task lifecycle status ---
status = "pending"  # pending | in_review | approved | completed

# --- Harness scenario (Phase 1: replayed under conditions A/B/C) ---

EXPECTED_POLICY = {"A": "off", "B": "off", "C": "balanced"}

SCENARIO_STEPS = [
    {
        "tool": "generic",
        "args": {},
        "output": "sutra config probe\n",
        "required": (),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    cfg = rep["cfg"]
    condition = rep["condition"]
    return {
        "eval-002-max-bytes-default": cfg["max_bytes"] == 24_576,
        "eval-002-reuse-min-bytes-default": cfg["reuse_min_bytes"] == 4096,
        "eval-002-store-enabled-matches-condition": (
            cfg["store_enabled"] == (condition != "A")),
        "eval-002-compression-enabled-matches-condition": (
            cfg["enabled"] == (condition != "A")),
        "eval-002-policy-mode": cfg["policy_mode"] == EXPECTED_POLICY[condition],
    }


env_overrides = {}
repeated_steps = False
