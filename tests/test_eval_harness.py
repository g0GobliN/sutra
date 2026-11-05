"""The model-free replay harness: deterministic, lossless and honest about modes."""

import pathlib
import sys

EVAL = pathlib.Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL))

import harness  # noqa: E402
import workloads  # noqa: E402


def test_replay_is_deterministic_and_loses_no_required_clue():
    selected = workloads.workloads()[:2]
    first = harness.run_all(selected, modes=("baseline", "output", "balanced"))
    second = harness.run_all(selected, modes=("baseline", "output", "balanced"))
    for mode in ("baseline", "output", "balanced"):
        assert first["totals"][mode]["input_context_tokens"] == \
            second["totals"][mode]["input_context_tokens"]
        assert first["totals"][mode]["visible_bytes"] == \
            second["totals"][mode]["visible_bytes"]
    quality = first["totals"]["balanced"]["quality"]
    assert quality["clues_total"] > 0
    assert quality["lost"] == 0
    assert quality["exact_restore_failures"] == 0
    assert quality["protected_ok"] == quality["protected_total"]
    assert quality["workloads_passed"] == len(selected)


def test_output_reduces_bytes_and_balanced_records_its_policy_cost():
    summary = harness.run_all(workloads.workloads()[:1],
                              modes=("baseline", "output", "balanced", "aggressive"))
    assert (summary["totals"]["output"]["visible_bytes"]
            < summary["totals"]["baseline"]["original_bytes"])
    assert summary["totals"]["output"]["policy_overhead_tokens"] == 0
    if not summary["environment"]["tokenizer"].startswith("tiktoken"):
        # CI does not install the optional benchmark dependency. Token metrics
        # must be explicitly unavailable, not silently zero.
        assert summary["totals"]["balanced"]["policy_tokens"] is None
        assert summary["totals"]["balanced"]["policy_overhead_tokens"] is None
        assert summary["savings"]["balanced"]["net_tokens"] is None
        assert summary["savings"]["balanced"]["net_savings_pct"] is None
        return
    assert summary["totals"]["balanced"]["policy_tokens"] > 0
    assert (summary["totals"]["aggressive"]["policy_tokens"]
            > summary["totals"]["balanced"]["policy_tokens"])
    # The policy is a cost: net tokens can never be below optimized tokens.
    for mode in ("balanced", "aggressive"):
        savings = summary["savings"][mode]
        assert savings["net_tokens"] == (
            savings["optimized_tokens"] + savings["policy_overhead_tokens"])
        assert savings["net_savings_pct"] <= savings["savings_pct"]


def test_repeated_reads_are_the_only_reuse_hits():
    workload = next(item for item in workloads.workloads()
                    if item.id == "repeated-file-inspection")
    output = harness.run_workload(workload, "output")
    balanced = harness.run_workload(workload, "balanced")
    assert output["reuse_hits"] == 0
    assert balanced["reuse_hits"] >= 2
    assert balanced["quality"]["lost"] == 0
    assert balanced["quality"]["exact_restore_failures"] == 0
