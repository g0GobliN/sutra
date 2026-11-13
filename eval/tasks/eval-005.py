"""eval/tasks/eval-005.py — Dependency troubleshooting: store orphan cleanup.

Task ID: eval-005
Format version: 0.6.2-task-v1
"""

from __future__ import annotations

from typing import Any

# --- Task identification ---
task_id = "eval-005"
task_type = "standard"
initial_snapshot = "git:tooltrim@HEAD"

# --- Task description ---
description = (
    "The store retains originals on disk. When a metadata file is corrupted, "
    "readers must treat that entry as missing while a valid sibling entry "
    "remains fully readable. Condition A keeps the store disabled, so no "
    "originals are retained there at all."
)

# --- Pre-defined acceptance criteria ---
acceptance_criteria = [
    "condition A: store.put() returns None (store disabled, no retention)",
    "conditions B and C: both put() calls retain their originals",
    "a corrupted metadata file makes get() report the entry as missing",
    "the valid sibling entry remains gettable with matching byte length",
    "store prune runs without error and its report is recorded",
]

# --- Expected behavior ---
expected_behavior = {
    "store_disabled_under_a": True,
    "corrupt_meta_get_status": "missing",
    "valid_entry_get_status": "ok",
}

validation_scripts = [
    {
        "name": "store_orphan_cleanup",
        "type": "automated",
        "command": "python eval/run_harness.py run --task eval-005 --reps 3",
        "description": "Put two entries, corrupt one metadata file, prune, and verify both get() outcomes under A/B/C.",
        "expected_pass": True,
    },
]

human_review = {
    "instructions": (
        "1. Confirm condition A retains nothing (put returns None).\n"
        "2. Confirm corrupted metadata is reported as missing by get().\n"
        "3. Confirm the uncorrupted entry still returns its exact bytes.\n"
        "4. Note: prune's orphan sweep is age-gated (about one hour), so a "
        "freshly corrupted entry may not be unlinked within this run.\n"
        "5. Rate difficulty: Trivial / Easy / Moderate / Hard / Complex."
    ),
    "difficulty_rating": "Moderate",
    "estimated_time_minutes": 25,
}

difficulty_rating = "Moderate"

classification = {
    "type": "standard",
    "reason": "Direct store API exercise; deterministic given the store's "
              "age-gated orphan sweep.",
    "adversarial_note": None,
}

evidence_level = "TESTED"
status = "pending"

# --- Harness scenario (direct store operations, not process_text) ---

_VALID_TEXT = ("valid retention target data — the harness must keep this "
               "original byte-for-byte\n")
_ORPHAN_TEXT = ("orphan payload data — about to lose its metadata\n")

SCENARIO_STEPS = [
    {
        "tool": "sutra_store",
        "args": {"op": "put", "text": _VALID_TEXT},
        "required": (),
        "protected": False,
    },
    {
        "tool": "sutra_store",
        "args": {"op": "put", "text": _ORPHAN_TEXT},
        "required": (),
        "protected": False,
    },
    {
        "tool": "sutra_store",
        "args": {"op": "corrupt_meta"},
        "required": (),
        "protected": False,
    },
    {
        "tool": "sutra_store",
        "args": {"op": "prune"},
        "required": (),
        "protected": False,
    },
    {
        "tool": "sutra_store",
        "args": {"op": "get", "id": "{0.id}"},
        "required": (),
        "protected": False,
    },
    {
        "tool": "sutra_store",
        "args": {"op": "get", "id": "{1.id}"},
        "required": (),
        "protected": False,
    },
]


def scenario() -> list[dict[str, Any]]:
    return list(SCENARIO_STEPS)


def verify(rep: dict[str, Any]) -> dict[str, bool]:
    condition = rep["condition"]
    steps = rep.get("steps", [])
    puts = [s for s in steps if s.get("store_op") == "put"]
    gets = [s for s in steps if s.get("store_op") == "get"]

    checks: dict[str, bool] = {}
    if condition == "A":
        checks["eval-005-store-disabled-under-control"] = all(
            not s.get("put_ok") for s in puts)
        return checks

    first_get = None
    orphan_get = None
    if puts:
        first_id = puts[0].get("id")
        orphan_id = puts[1].get("id") if len(puts) > 1 else None
        for g in gets:
            if g.get("target") == first_id:
                first_get = g
            elif orphan_id is not None and g.get("target") == orphan_id:
                orphan_get = g

    checks["eval-005-puts-retained"] = all(s.get("put_ok") for s in puts)
    if first_get is not None:
        checks["eval-005-valid-entry-preserved"] = (
            first_get.get("get_status") == "ok"
            and first_get.get("get_entry") is True)
    if orphan_get is not None:
        checks["eval-005-corrupt-meta-reads-missing"] = (
            orphan_get.get("get_status") == "missing")
    return checks


env_overrides = {}
repeated_steps = False
