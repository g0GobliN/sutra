"""eval/tasks/eval-003.py — Test failure diagnosis: clue recoverability.

Task ID: eval-003
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from typing import Any

# --- Task identification ---
task_id = "eval-003"
task_type = "standard"
initial_snapshot = "git:tooltrim@HEAD"

# --- Task description ---
description = (
    "A test run emits a 147 KB tool result with one failure clue buried in the "
    "middle. Under condition A the agent sees everything raw. Under B and C the "
    "result is elided around the clue, so the clue must still be recoverable "
    "from the retained original (search recovery) and the original must restore "
    "byte-for-byte."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "condition A shows the clue in the visible (raw) output",
    "conditions B and C elide the clue but mark the result with a recovery id",
    "the clue is recoverable by searching the stored original under B and C",
    "the stored original restores byte-exact under B and C",
    "no clue is lost under any condition",
]

# --- Expected behavior ---
expected_behavior = {
    "visible_reduction_b_c": "roughly 85-90% of bytes elided under B/C",
    "recovery_accuracy": "100% byte-exact recovery",
    "clue_state": {"A": "visible", "B": "recoverable", "C": "recoverable"},
}

# --- Validation scripts ---
validation_scripts = [
    {
        "name": "clue_recoverability",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-003 --reps 3",
        "description": "Replay the 147 KB log under A/B/C and check clue state and byte-exact restore.",
        "expected_pass": True,
    },
]

# --- Human review instructions ---
human_review = {
    "instructions": (
        "1. Confirm the elided result carries a marker with an id under B/C.\n"
        "2. Confirm sutra_restore(id=..., mode='search', query=...) returns the clue.\n"
        "3. Confirm sutra_restore(id=..., mode='bytes', full range) equals the original.\n"
        "4. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex."
    ),
    "difficulty_rating": "Moderate",
    "estimated_time_minutes": 30,
}

difficulty_rating = "Moderate"

classification = {
    "type": "standard",
    "reason": "Deterministic payload; recovery is exercised through public "
              "restore APIs only.",
    "adversarial_note": None,
}

evidence_level = "TESTED"
status = "pending"

# --- Harness scenario ---
_LOG_LINES = 3000
_CLUE_LINE_INDEX = 1500
_CLUE = "CLUE: seam-token-3M-8821"


def _log_text() -> str:
    lines = [
        f"2026-11-04T09:{i % 60:02d}Z runner worker-{i // 60 % 24:02d} "
        f"case {i:06d} ok\n"
        for i in range(_LOG_LINES)
    ]
    lines[_CLUE_LINE_INDEX] = f"{_CLUE} — failing assertion at line {_CLUE_LINE_INDEX}\n"
    return "".join(lines)


SCENARIO_STEPS = [
    {
        "tool": "bash",
        "args": {},
        "output": _log_text(),
        "required": (_CLUE,),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    state = (
        rep["steps"][0]["clues"][0]["state"]
        if rep.get("steps") and rep["steps"][0].get("clues")
        else "missing"
    )
    expected = "visible" if rep["condition"] == "A" else "recoverable"
    return {"eval-003-clue-state": state == expected}


env_overrides = {}
repeated_steps = False
