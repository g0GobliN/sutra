"""eval/tasks/eval-001.py — Human bug fix: off-by-one in cost engine boundary"""

from typing import Any

# Task ID: eval-001
# Format version: 0.6.2-task-v1
# Initial repo snapshot: git:sutra@a1b2c3d4e5f6 (commit before task env)
task_id = "eval-001"
task_type = "bug_fix"

# --- Initial repo snapshot reference ---
initial_snapshot = "git:sutra@a1b2c3d4e5f6"

# --- Task description ---
description = (
    "The SUTRA cost engine in `sutra/engine.py` has an off-by-one error in its\n"
    "compression boundary calculation. When tool results are exactly at the page\n"
    "boundary (multiples of 65_536 bytes), the first byte of the next page is\n"
    "omitted during recovery. Fix the boundary condition so that all original bytes\n"
    "are preserved during compression+recovery cycles."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "compress then recover a 65_536-byte payload preserves all original bytes",
    "compress then recover a 65_537-byte payload preserves all original bytes",
    "compress then recover a 131_072-byte payload preserves all original bytes",
    "protected tool results remain unchanged after SUTRA processing",
    "no regression in baseline (control) mode performance",
    "evidence level: EXPERIMENTALLY DEMONSTRATED after 3 reps",
]

# --- Expected behavior ---
expected_behavior = {
    "compression_pct": "approximately 40-55% reduction in visible bytes",
    "recovery_accuracy": "100% byte-exact recovery for all payload sizes",
    "policy_overhead": "within 5% of baseline token usage",
    "task_pass": True,  # all clues visible after recovery
}

# --- Validation scripts (automated where possible) ---
validation_scripts = [
    {
        "name": "boundary_test",
        "type": "automated",
        "command": "python -m pytest tests/ -k boundary -v",
        "description": "Run boundary condition tests for compression page edges",
        "expected_pass": True,
    },
    {
        "name": "smoke_test",
        "type": "automated",
        "command": "python -c \"from sutra import; print('import OK')\"",
        "description": "Verify the module imports without error",
        "expected_pass": True,
    },
    {
        "name": "human_review",
        "type": "human",
        "instructions": "Verify that the fix correctly handles the 65_536-byte boundary case by reviewing the changed function and confirming the off-by-one correction.",
        "expected_outcome": "Describe in a comment whether the fix is correct.",
    },
]

# --- Human review instructions ---
human_review = {
    "instructions": (
        "1. Apply the fix to the repo.\n"
        "2. Run the eight workloads in baseline, output, balanced, and aggressive modes.\n"
        "3. Verify that no clues are lost at page boundaries (65_536k boundaries).\n"
        "4. Confirm protected tool results are preserved.\n"
        "5. Rate the fix difficulty: Trivial / Easy / Moderate / Hard.\n"
        "6. Add any observed edge cases or unintended side effects."
    ),
    "difficulty_rating": "Moderate",  # Trivial | Easy | Moderate | Hard | Complex
    "estimated_time_minutes": 45,
}

# --- Expected difficulty rating ---
difficulty_rating = "Moderate"  # Trivial | Easy | Moderate | Hard | Complex

# --- Adversarial vs standard classification ---
classification = {
    "type": "standard",  # standard | adversarial
    "reason": "Standard bug fix with well-defined boundary condition. "
              "No adversarial inputs or hidden failure modes.",
    "adversarial_note": None,
}

# --- Evidence tracking ---
evidence_level = "IMPLEMENTED"  # IMPLEMENTED | TESTED | EXPERIMENTALLY DEMONSTRATED | PRODUCTION VERIFIED

# --- Task lifecycle status ---
status = "pending"  # pending | in_review | approved | completed


# --- Harness scenario (Phase 1: replayed under conditions A/B/C) ---
# Each step is one tool result fed through sutra.process_text. The harness
# verifies byte-exact recovery, clue recoverability, and compression behavior
# at the compression boundary (24_576 bytes = SUTRA_MAX_BYTES default).


_UNIT = b"payload-data-"


def _payload(total_bytes: int, clue: str, offset: int) -> str:
    """Build a payload of exactly ``total_bytes`` UTF-8 bytes with ``clue``
    starting at byte ``offset``, padded with a fixed filler unit."""
    clue_b = clue.encode("utf-8")
    after_len = total_bytes - offset - len(clue_b)
    if after_len < 0:
        raise ValueError("clue does not fit the payload size")
    before = (_UNIT * (offset // len(_UNIT) + 1))[:offset]
    after = (_UNIT * (after_len // len(_UNIT) + 1))[:after_len]
    return (before + clue_b + after).decode("utf-8")


SCENARIO_STEPS = [
    {
        "tool": "read_file",
        "args": {},
        "output": _payload(24_576, "eval-001-boundary-equal", 15_000),
        "required": ("eval-001-boundary-equal",),
        "protected": False,
    },
    {
        "tool": "read_file",
        "args": {},
        "output": _payload(24_577, "eval-001-over-by-one", 15_000),
        "required": ("eval-001-over-by-one",),
        "protected": False,
    },
    {
        "tool": "bash",
        "args": {},
        "output": _payload(50_000, "eval-001-large-payload", 25_000),
        "required": ("eval-001-large-payload",),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    steps = rep.get("steps", [])
    condition = rep["condition"]
    checks: dict[str, bool] = {}
    if len(steps) < 3:
        return checks
    first, second, third = steps[0], steps[1], steps[2]
    checks["eval-001-boundary-unchanged"] = first["changed"] is False
    checks["eval-001-boundary-bytes-preserved"] = (
        first["original_bytes"] == first["visible_bytes"] == 24_576)
    if condition == "A":
        # The control passes everything through raw: no compression, no ids.
        checks["eval-001-raw-passthrough"] = all(
            not s["changed"] and s["id"] is None for s in steps)
        checks["eval-001-clues-visible-under-control"] = all(
            c["state"] == "visible" for s in steps for c in s["clues"])
    else:
        checks["eval-001-over-compressed"] = (
            second["changed"] is True
            and bool(second["id"])
            and second["compressed_bytes"] < second["original_bytes"])
        checks["eval-001-over-clue-recoverable"] = any(
            c["state"] == "recoverable" for c in second["clues"])
        checks["eval-001-large-compressed"] = third["changed"] is True
        checks["eval-001-large-clue-recoverable"] = any(
            c["state"] == "recoverable" for c in third["clues"])
    return checks


env_overrides = {}
repeated_steps = False

# vim: set ts=4 sw=4 expandtab :