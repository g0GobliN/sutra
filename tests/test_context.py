"""Tests for the PART 8 context intelligence engine."""

from __future__ import annotations

import itertools
import random

import pytest

from conftest import plugin_module

context = plugin_module("context")


def item(item_id: str, content: str = "", **kwargs) -> "context.Item":
    kwargs.setdefault("token_cost", 1)
    return context.Item(id=item_id, content=content or item_id, **kwargs)


def test_fingerprint_is_sha256_of_the_content():
    assert context.fingerprint("abc") == context.fingerprint("abc")
    assert context.fingerprint("abc") != context.fingerprint("abd")
    assert len(context.fingerprint("")) == 64


def test_item_fills_hash_and_rejects_bad_values():
    made = item("a")
    assert made.hash == context.fingerprint(made.content)
    with pytest.raises(ValueError):
        item("a", relevance=1.5)
    with pytest.raises(ValueError):
        item("a", token_cost=-1)
    with pytest.raises(ValueError):
        item("a", dependency=("a",))
    with pytest.raises(ValueError):
        item("")


def test_item_from_dict_accepts_dependency_forms():
    single = context.Item.from_dict(
        {"id": "a", "content": "x", "dependency": "b"})
    many = context.Item.from_dict(
        {"id": "a", "content": "x", "dependencies": ["b", "c"]})
    missing = context.Item.from_dict({"id": "a"})
    assert single.dependency == ("b",)
    assert many.dependency == ("b", "c")
    assert missing.dependency == ()
    assert missing.content == ""


def test_cost_uses_reported_tokens_or_a_flagged_estimate():
    measured = item("a", "x" * 41, token_cost=7)
    assert measured.cost() == 7
    assert not measured.estimated()
    guessed = item("b", "x" * 41, token_cost=None)
    assert guessed.cost() == 10  # 41 bytes // 4
    assert guessed.estimated()
    assert guessed.cost("bytes") == 41
    assert not guessed.estimated("bytes")


def test_values_use_the_documented_weighted_sum():
    items = [
        item("a", relevance=1.0, importance=0.0, usefulness=0.0),
        item("b", relevance=0.0, importance=1.0, usefulness=0.0),
    ]
    scored = context.values(items, context.Weights(
        relevance=0.5, importance=0.5, dependency=0.0, usefulness=0.0,
        recency=0.0))
    assert scored["a"] == pytest.approx(0.5)
    assert scored["b"] == pytest.approx(0.5)


def test_recency_is_normalized_within_the_batch():
    items = [
        item("old", last_updated=100.0),
        item("mid", last_updated=110.0),
        item("new", last_updated=120.0),
        item("unknown"),
    ]
    scored = context.values(items, context.Weights(
        relevance=0.0, importance=0.0, dependency=0.0, usefulness=0.0,
        recency=1.0))
    assert scored["old"] == pytest.approx(0.0)
    assert scored["mid"] == pytest.approx(0.5)
    assert scored["new"] == pytest.approx(1.0)
    assert scored["unknown"] == pytest.approx(0.0)


def test_explicit_recency_wins_over_timestamps():
    items = [item("a", last_updated=0.0, recency=0.9), item("b", last_updated=9.0)]
    scored = context.values(items, context.Weights(
        relevance=0.0, importance=0.0, dependency=0.0, usefulness=0.0,
        recency=1.0))
    assert scored["a"] == pytest.approx(0.9)


def test_dependency_value_counts_transitive_dependents():
    items = [item("a"), item("b", dependency=("a",)),
             item("c", dependency=("b",))]
    assert context.dependency_values(items) == {
        "a": 1.0, "b": 0.5, "c": 0.0}


def test_cycle_is_reported_with_its_path():
    items = [item("a", dependency=("b",)), item("b", dependency=("a",))]
    found = context.detect_cycle(items)
    assert found is not None
    assert found[0] == found[-1]
    with pytest.raises(context.CycleError):
        context.select(items, 10)


def test_mandatory_items_and_their_dependencies_are_kept():
    items = [item("dep"), item("must", token_cost=1, dependency=("dep",),
                               mandatory=True),
             item("other", token_cost=5)]
    result = context.select(items, budget=2)
    assert result.selected == ("dep", "must")
    assert result.feasible
    assert result.algorithm == "exact-knapsack"


def test_impossible_mandatory_budget_is_reported_not_hidden():
    items = [item("must", token_cost=10, mandatory=True), item("other")]
    result = context.select(items, budget=5)
    assert result.feasible is False
    assert result.selected == ("must",)
    assert "mandatory" in (result.conflict or "")
    assert result.algorithm == "mandatory-conflict"


def test_missing_optional_dependency_is_dropped_with_a_reason():
    items = [item("a", dependency=("gone",)), item("b")]
    result = context.select(items, budget=10)
    assert "a" not in result.selected
    assert dict(result.dropped)["a"] == "missing dependency"
    assert "b" in result.selected


def test_dependency_closure_selection_respects_the_budget():
    items = [item("a", token_cost=3, relevance=0.2),
             item("b", token_cost=3, relevance=0.1),
             item("c", token_cost=3, relevance=1.0, dependency=("a",))]
    result = context.select(items, budget=6)
    assert result.total_cost <= 6
    assert "c" in result.selected
    assert "a" in result.selected
    assert result.algorithm in ("greedy-closure", "greedy-optimal")


def test_exact_selection_matches_brute_force():
    rng = random.Random(7)
    for _ in range(50):
        count = rng.randint(0, 8)
        items = [
            context.Item(id=f"i{index}", content="x" * index,
                         token_cost=rng.randint(1, 6),
                         relevance=rng.random(),
                         importance=rng.random())
            for index in range(count)
        ]
        budget = rng.randint(0, 15)
        result = context.select(items, budget)
        best = 0.0
        for size in range(count + 1):
            for subset in itertools.combinations(items, size):
                if sum(member.token_cost for member in subset) <= budget:
                    best = max(best, sum(
                        context.values(items)[member.id] for member in subset))
        assert result.total_cost <= budget
        assert result.value == pytest.approx(best, abs=1e-9)


def test_deterministic_selection_and_ordering():
    items = [item("b", relevance=0.5), item("a", relevance=0.5),
             item("c", relevance=0.1)]
    first = context.select(items, budget=2).as_dict()
    second = context.select(list(reversed(items)), budget=2).as_dict()
    assert first == second
    assert first["selected"] == ["a", "b"]


def test_near_duplicates_are_detected_but_distant_texts_are_not():
    first = "the quick brown fox jumps over the lazy dog again and again"
    second = "the quick brown fox jumps over the lazy dog again and again!"
    distant = "completely different words appear in this unrelated sentence"
    near = context.hamming(context.simhash(first), context.simhash(second))
    assert near <= context.DEFAULT_NEAR_DISTANCE
    assert context.hamming(context.simhash(first),
                           context.simhash(distant)) > context.DEFAULT_NEAR_DISTANCE


def test_near_duplicates_are_optional_and_reported():
    text = "same tool output repeated with one trailing difference"
    items = [item("keep", text, relevance=0.9),
             item("dup", text + "!", relevance=0.8)]
    plain = context.select(items, budget=10)
    assert plain.selected == ("dup", "keep")
    deduped = context.select(items, budget=10, near_duplicates=True)
    assert deduped.selected == ("keep",)
    assert deduped.duplicates == (("dup", "keep"),)
    assert "near-duplicate" in dict(deduped.dropped)["dup"]


def test_near_duplicates_with_dependents_are_never_dropped():
    text = "one identical payload shared by two context items here"
    items = [item("base", text), item("user", text + "!", dependency=("base",))]
    result = context.select(items, budget=10, near_duplicates=True)
    assert "base" in result.selected


def test_selection_marks_estimated_costs():
    items = [context.Item(id="a", content="x" * 40)]
    result = context.select(items, budget=10)
    assert result.selected == ("a",)
    assert result.estimated is True


def test_bytes_unit_uses_byte_lengths():
    items = [context.Item(id="a", content="x" * 40)]
    result = context.select(items, budget=40, unit="bytes")
    assert result.selected == ("a",)
    assert result.total_cost == 40
    assert result.estimated is False


def test_context_updates_only_refingerprint_changed_content():
    box = context.Context([item("a", "first")])
    original = box.get("a")
    same = box.update("a", relevance=0.5)
    assert same.hash == original.hash
    changed = box.update("a", content="second")
    assert changed.hash != original.hash
    assert changed.hash == context.fingerprint("second")


def test_context_remove_and_selection_delegate():
    box = context.Context([item("a"), item("b")])
    assert len(box) == 2
    assert "a" in box
    assert box.remove("a").id == "a"
    assert [entry.id for entry in box.items()] == ["b"]
    result = box.select(1)
    assert result.selected == ("b",)


def test_select_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        context.select([item("a"), item("a")], 5)


def test_empty_and_zero_budget():
    assert context.select([], 0).algorithm == "empty"
    result = context.select([item("a")], 0)
    assert result.selected == ()
    assert result.dropped == (("a", "not selected: budget or lower value"),)


def test_minhash_jaccard_approximates_similarity():
    left = context.minhash("alpha beta gamma delta epsilon zeta")
    right = context.minhash("alpha beta gamma delta epsilon zeta")
    other = context.minhash("one two three four five six seven eight")
    assert context.jaccard(left, right) == pytest.approx(1.0)
    assert context.jaccard(left, other) < 0.5


def test_cli_context_reads_json_and_reports_selection(capsys, monkeypatch):
    import io
    import json

    cli = plugin_module("cli")
    payload = {
        "budget": 2,
        "items": [
            {"id": "a", "content": "x", "token_cost": 1, "relevance": 1.0},
            {"id": "b", "content": "y", "token_cost": 1, "relevance": 0.1},
            {"id": "c", "content": "z", "token_cost": 1, "relevance": 0.2},
        ],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = cli.main(["context", "--budget", "2", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["selected"] == ["a", "c"]
    assert report["algorithm"] == "exact-knapsack"


def test_cli_context_reports_a_mandatory_conflict(capsys, monkeypatch):
    import io
    import json

    cli = plugin_module("cli")
    payload = [{"id": "must", "content": "x", "token_cost": 5,
                "mandatory": True}]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = cli.main(["context", "--budget", "1"])
    captured = capsys.readouterr()
    assert code == 1
    assert "budget conflict" in captured.err


def test_cli_context_rejects_malformed_input(capsys, monkeypatch):
    import io

    cli = plugin_module("cli")
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    code = cli.main(["context", "--budget", "1"])
    assert code == 2
    assert "sutra context" in capsys.readouterr().err


def test_near_duplicate_with_distinct_vocabulary_is_kept():
    base = " ".join(f"token{i}" for i in range(40))
    kept = context.Item(id="kept", content=base, token_cost=1, relevance=0.9)
    twin = context.Item(id="twin", content=base + " extra", token_cost=1,
                        relevance=0.1)
    distinct = context.Item(id="distinct", content=base + " " + " ".join(
        f"rare{i}" for i in range(20)), token_cost=1, relevance=0.2)
    # Force all three into one SimHash group so the vocabulary guard, not the
    # bit distance, decides what may be dropped.
    selection = context.select([kept, twin, distinct], 10, unit="tokens",
                              near_duplicates=True, near_distance=64)
    dropped = dict(selection.dropped)
    assert "twin" in dropped and "distinct" not in dropped
    assert ("twin", "kept") in selection.duplicates
    assert "distinct" in selection.selected
