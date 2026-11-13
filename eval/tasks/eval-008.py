"""eval/tasks/eval-008.py — Configuration debugging: policy mode resolution.

Task ID: eval-008
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

# --- Task identification ---
task_id = "eval-008"
task_type = "standard"
initial_snapshot = "git:tooltrim@HEAD"

# --- Task description ---
description = (
    "The policy engine selects compression behavior through SUTRA_POLICY: off "
    "(control and compression+recovery) or balanced (full optimization). The "
    "resolved mode must match the condition contract exactly, and policy.mode() "
    "must agree with config.resolve().policy_mode."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "condition A resolves policy mode off",
    "condition B resolves policy mode off",
    "condition C resolves policy mode balanced",
    "policy.mode(cfg) agrees with config.resolve().policy_mode under every condition",
    "no errors raised while resolving config under any condition",
]

# --- Expected behavior ---
expected_behavior = {
    "policy_mode": {"A": "off", "B": "off", "C": "balanced"},
}

validation_scripts = [
    {
        "name": "policy_resolution",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-008 --reps 3",
        "description": "Assert policy.mode() matches the expected mode under A/B/C.",
        "expected_pass": True,
    },
]

human_review = {
    "instructions": (
        "1. Confirm SUTRA_POLICY=off keeps policy.mode() == 'off' (A and B).\n"
        "2. Confirm SUTRA_POLICY=balanced yields 'balanced' (C).\n"
        "3. Confirm config resolution never raises under any condition.\n"
        "4. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex."
    ),
    "difficulty_rating": "Easy",
    "estimated_time_minutes": 10,
}

difficulty_rating = "Easy"

classification = {
    "type": "standard",
    "reason": "Read-only policy/config inspection.",
    "adversarial_note": None,
}

evidence_level = "TESTED"
status = "pending"

# --- Harness scenario ---

EXPECTED_MODE = {"A": "off", "B": "off", "C": "balanced"}

SCENARIO_STEPS = [
    {
        "tool": "generic",
        "args": {},
        "output": "sutra policy probe\n",
        "required": (),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    from sutra import policy as _policy

    cfg = SimpleNamespace(**rep["cfg"])
    expected = EXPECTED_MODE[rep["condition"]]
    return {
        "eval-008-policy-mode": _policy.mode(cfg) == expected,
    }


env_overrides = {}
repeated_steps = False
