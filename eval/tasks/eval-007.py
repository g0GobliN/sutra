"""eval/tasks/eval-007.py — Build failure investigation: protected payloads + reuse.

Task ID: eval-007
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from typing import Any

# --- Task identification ---
task_id = "eval-007"
task_type = "standard"
initial_snapshot = "git:tooltrim@HEAD"

# --- Task description ---
description = (
    "A build failure produces a unified diff that must survive every SUTRA "
    "condition byte-for-byte (protected tool results are never rewritten), "
    "followed by a large build log that is compressed under B/C and served as "
    "a reuse marker under C when it repeats exactly."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "the protected diff stays byte-identical under A, B, and C",
    "the build log is passed through raw under A",
    "the build log is compressed (and stored) under B and C",
    "under C, the repeated build log is served from the reuse index",
    "under B, the repeated build log is compressed again (no reuse index)",
]

# --- Expected behavior ---
expected_behavior = {
    "protected_ok": True,
    "reuse": {"A": False, "B": False, "C": True},
}

validation_scripts = [
    {
        "name": "protected_and_reuse",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-007 --reps 3",
        "description": "Replay a protected diff plus a repeated large build output under A/B/C.",
        "expected_pass": True,
    },
]

human_review = {
    "instructions": (
        "1. Confirm protected results are never compressed or marked.\n"
        "2. Confirm condition C replaces the repeated large result with a reuse marker.\n"
        "3. Confirm condition B recompresses it instead.\n"
        "4. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex."
    ),
    "difficulty_rating": "Moderate",
    "estimated_time_minutes": 25,
}

difficulty_rating = "Moderate"

classification = {
    "type": "standard",
    "reason": "Deterministic payloads; reuse index is process-scoped and reset "
              "between reps.",
    "adversarial_note": None,
}

evidence_level = "IMPLEMENTED"
status = "pending"

# --- Harness scenario ---
_BUILD_LINE = "  CC sutra/engine.c\n"


def _build_output() -> str:
    # 5000 lines x 19 bytes = 95 KB: above max_bytes (compressed/stored) and
    # above reuse_min_bytes (4096, eligible for the reuse index).
    return _BUILD_LINE * 5000


_DIFF_LINES = [
    "diff --git a/sutra/engine.py b/sutra/engine.py",
    "--- a/sutra/engine.py",
    "+++ b/sutra/engine.py",
    "@@ -10,6 +10,7 @@",
    " context line one",
    "- old implementation detail",
    "+ new implementation detail",
    " context line two",
] + [f" context line {i}\n" for i in range(3, 40)]
_DIFF = "\n".join(_DIFF_LINES) + "\n"


SCENARIO_STEPS = [
    {
        "tool": "read_file",
        "args": {},
        "output": _DIFF,
        "required": (),
        "protected": True,
    },
    {
        "tool": "bash",
        "args": {},
        "output": _build_output(),
        "required": (),
        "protected": False,
    },
    {
        "tool": "bash",
        "args": {},
        "output": _build_output(),
        "required": (),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    steps = rep.get("steps", [])
    condition = rep["condition"]
    checks: dict[str, bool] = {}
    if len(steps) >= 3:
        checks["eval-007-protected-preserved"] = (
            steps[0]["visible_bytes"] == steps[0]["original_bytes"]
            and steps[0]["changed"] is False)
        expected_changed = condition != "A"
        checks["eval-007-build-treated-consistently"] = (
            steps[1]["changed"] == expected_changed)
        if condition == "C":
            checks["eval-007-reuse-engaged"] = steps[2]["reused"] is True
        elif condition == "B":
            checks["eval-007-no-reuse-under-b"] = steps[2]["reused"] is False
        else:
            checks["eval-007-raw-under-control"] = steps[1]["changed"] is False
    return checks


env_overrides = {}
repeated_steps = False
