"""eval/tasks/eval-006.py — Log analysis: large log, narrow recovery.

Task ID: eval-006
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from typing import Any

# --- Task identification ---
task_id = "eval-006"
task_type = "standard"
initial_snapshot = "git:sutra@HEAD"

# --- Task description ---
description = (
    "An application log of several hundred kilobytes contains one error line. "
    "Under B and C the log is compressed away around the error, yet the full "
    "original must page-restore byte-exact and the error must remain "
    "searchable in the retained original."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "condition A shows the error line in the visible raw output",
    "conditions B and C compress the log and mark it with a recovery id",
    "the stored log pages back byte-exact across many restore calls",
    "the error line is recoverable by searching the stored original under B/C",
    "visible bytes drop by roughly an order of magnitude under B and C",
]

# --- Expected behavior ---
expected_behavior = {
    "original_bytes": "about 540-560 KB",
    "visible_reduction_b_c": "roughly 95%+ elided under B/C",
    "recovery_accuracy": "100% byte-exact multi-page recovery",
    "clue_state": {"A": "visible", "B": "recoverable", "C": "recoverable"},
}

validation_scripts = [
    {
        "name": "large_log_recovery",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-006 --reps 3",
        "description": "Replay the multi-hundred-KB log under A/B/C and verify multi-page restore plus search recovery.",
        "expected_pass": True,
    },
]

human_review = {
    "instructions": (
        "1. Confirm the elided marker quotes a recovery id under B/C.\n"
        "2. Page through the original with mode='bytes' and confirm byte equality.\n"
        "3. Confirm mode='search' for the error line returns a match.\n"
        "4. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex."
    ),
    "difficulty_rating": "Moderate",
    "estimated_time_minutes": 30,
}

difficulty_rating = "Moderate"

classification = {
    "type": "standard",
    "reason": "Large deterministic payload; no adversarial inputs.",
    "adversarial_note": None,
}

evidence_level = "TESTED"
status = "pending"

# --- Harness scenario ---
_LOG_LINES = 8000
_ERROR_LINE_INDEX = 6000
_ERROR_LINE = ("2026-11-04T09:12:00Z ERROR upstream timeout after 30s — "
               "retry policy has no jitter")
_CLUE = "upstream timeout after 30s"


def _log_text() -> str:
    lines = [
        f"2026-11-04T09:{i % 60:02d}Z app server-{i % 16:02d} "
        f"request {i:06d} served in {i % 50}ms\n"
        for i in range(_LOG_LINES)
    ]
    lines[_ERROR_LINE_INDEX] = _ERROR_LINE + "\n"
    return "".join(lines)


_GREP_OUTPUT = _ERROR_LINE + "\n"
_SED_OUTPUT = "\n".join(
    f"2026-11-04T09:{i % 60:02d}Z app server-{i % 16:02d} context {i:04d}\n"
    for i in range(5))


SCENARIO_STEPS = [
    {
        "tool": "bash",
        "args": {},
        "output": _log_text(),
        "required": (_CLUE,),
        "protected": False,
    },
    {
        "tool": "bash",
        "args": {},
        "output": _GREP_OUTPUT,
        "required": (),
        "protected": False,
    },
    {
        "tool": "bash",
        "args": {},
        "output": _SED_OUTPUT,
        "required": (),
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
    return {"eval-006-clue-state": state == expected}


env_overrides = {}
repeated_steps = False
