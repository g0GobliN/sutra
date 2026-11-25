"""Tests for the optimize module: bounded maximum-effort post-compression optimization."""

from conftest import load_plugin, plugin_module


def load_and_get_optimize():
    load_plugin()
    optimize = plugin_module("optimize")
    return optimize


optimize = load_and_get_optimize()


def test_crlf_normalization():
    """CRLF text is normalized and the report records 'crlf' applied."""
    text = "line1\r\nline2\r\nline3"
    result = optimize.optimize(text)
    # Check that 'crlf' was applied
    assert "crlf" in result.applied, f"Expected 'crlf' in applied, got {result.applied}"
    # Text should be normalized
    assert "\r" not in result.text, f"CRLF not normalized: {result.text!r}"


def test_blank_runs_outside_fence():
    """A run of 5 blank lines becomes 2 outside a fence."""
    text = "line1\n\n\n\n\nline2\n"
    result = optimize.optimize(text)
    # 5 blank lines should become 2 outside fence
    # The collapsed result should have at most 2 consecutive blank lines outside fences
    assert "blank-runs" in result.applied, f"Expected 'blank-runs' in applied, got {result.applied}"


def test_blank_runs_inside_fence():
    """A run of 5 blank lines inside a ``` fence is left untouched."""
    text = "line1\n\n\n\n\n```\n\n\n\n\n\ncontent\n```\nline2\n"
    result = optimize.optimize(text)
    # Fence content should be byte-identical (blank lines inside preserved)
    assert "blank-runs" in result.applied, f"Expected 'blank-runs' in applied, got {result.applied}"
    # The ``` fence content should be preserved
    assert "```\n\n\n\n\n\ncontent\n```" in result.text, f"Fence content modified: {result.text!r}"


def test_trailing_space_outside_fence():
    """Trailing spaces stripped outside a fence."""
    text = "line1   \nline2\n   \n"
    result = optimize.optimize(text)
    assert "trailing-space" in result.applied, f"Expected 'trailing-space' in applied, got {result.applied}"
    # Lines outside fence should have trailing spaces stripped
    assert "line1   " not in result.text, f"Trailing spaces not stripped: {result.text!r}"


def test_trailing_space_inside_fence():
    """Trailing spaces kept inside a fence."""
    text = "line1   \n```\nline2   \n```\nline3\n"
    result = optimize.optimize(text)
    assert "trailing-space" in result.applied, f"Expected 'trailing-space' in applied, got {result.applied}"
    # Trailing spaces inside fence should be preserved
    assert "line2   " in result.text, f"Trailing spaces inside fence removed: {result.text!r}"


def test_indent_trim_outside_fence():
    """Leading 20-space indentation outside a fence becomes 8."""
    # 20 spaces
    text20 = " " * 20 + "code()\nline2\n"
    result = optimize.optimize(text20)
    assert "indent-trim" in result.applied, f"Expected 'indent-trim' in applied, got {result.applied}"
    # Should be trimmed to 8 spaces
    assert result.text.startswith("        code()") or result.text.startswith(" " * 8 + "code()"), \
        f"Indent not trimmed: {result.text!r}"


def test_indent_inside_fence_unchanged():
    """Leading indentation inside a fence is unchanged."""
    text = " " * 20 + "top\n" + "```\n    indented\n```\nline2\n"
    result = optimize.optimize(text)
    assert "indent-trim" in result.applied, f"Expected 'indent-trim' in applied, got {result.applied}"
    # Indentation inside fence should be unchanged
    assert "    indented" in result.text, f"Indent inside fence changed: {result.text!r}"


def test_unicode_chars():
    """Unicode NBSP/zero-width characters replaced."""
    text = "hello\u00a0world\u200btest"
    result = optimize.optimize(text)
    assert "unicode-space" in result.applied, f"Expected 'unicode-space' in applied, got {result.applied}"
    # NBSP and ZWSP should be replaced
    assert "\u00a0" not in result.text, f"NBSP not removed: {result.text!r}"
    assert "\u200b" not in result.text, f"ZWSP not removed: {result.text!r}"


def test_sutra_markers_never_modified():
    """Marker lines starting with '[sutra:' are never modified."""
    text = "[sutra: recovery marker]\nline2\n"
    result = optimize.optimize(text)
    # A marker line is never rewritten, and nothing else here needs a change.
    assert result.applied == (), f"Expected nothing applied, got {result.applied}"
    assert result.text == text, f"Sutra marker run modified: {result.text!r}"


def test_custom_strategy_rejected_by_validator():
    """A custom strategy that deletes a protected path is rejected by the default validator."""
    Strategy = optimize.Strategy

    def delete_path_strategy(text: str) -> str:
        """Strategy that removes /etc/hosts - this is destructive."""
        return text.replace("/etc/hosts", "")

    custom_strat = Strategy(
        name="delete-path",
        apply=delete_path_strategy,
        note="Removes /etc/hosts",
    )

    text = "Hello /etc/hosts world\n"
    result = optimize.optimize(text, strategies=[custom_strat])
    # The custom strategy should be rejected by the default validator
    accepted_evals = [e for e in result.evaluations if e.name == "delete-path"]
    assert len(accepted_evals) > 0
    assert not accepted_evals[0].accepted, \
        f"Strategy should be rejected, but got accepted={accepted_evals[0].accepted}"
    assert "loss" in accepted_evals[0].reason.lower() or "path" in accepted_evals[0].reason.lower(), \
        f"Reason should mention loss: {accepted_evals[0].reason}"


def test_strategy_raised_recorded_as_rejected():
    """A strategy that raises is recorded as rejected and does not abort other strategies."""
    Strategy = optimize.Strategy

    def raising_strategy(text: str) -> str:
        raise ValueError("deliberate error")

    custom_strat = Strategy(
        name="raising-strat",
        apply=raising_strategy,
        note="Deliberately raises",
    )

    text = "line1\nline2\n"
    result = optimize.optimize(text, strategies=[custom_strat])
    raising_evals = [e for e in result.evaluations if e.name == "raising-strat"]
    assert len(raising_evals) > 0
    assert not raising_evals[0].accepted
    assert "raised" in raising_evals[0].reason.lower() or "error" in raising_evals[0].reason.lower()


def test_max_passes_one():
    """max_passes=1 applies at most one strategy."""
    text = "line1\nline2\nline3\n"
    result = optimize.optimize(text, max_passes=1)
    # Should apply at most one strategy
    assert len(result.applied) <= 1, f"Expected at most 1 applied, got {result.applied}"


def test_max_passes_zero():
    """max_passes=0 applies none and returns input unchanged."""
    text = "line1\nline2\n"
    result = optimize.optimize(text, max_passes=0)
    assert result.text == text, f"Expected original text, got {result.text!r}"
    assert result.applied == (), f"Expected no applied, got {result.applied}"


def test_determinism():
    """Two calls produce identical applied and text."""
    text = "line1\n  line2\n  \nline3\n"
    result1 = optimize.optimize(text)
    result2 = optimize.optimize(text)
    assert result1.applied == result2.applied, \
        f"Not deterministic: {result1.applied} vs {result2.applied}"
    assert result1.text == result2.text, \
        f"Not deterministic: {result1.text!r} vs {result2.text!r}"


def test_saved_bytes_never_negative():
    """saved_bytes == len(input) - len(result.text), never negative."""
    text = "line1\nline2\n"
    result = optimize.optimize(text)
    expected_saved = len(text) - len(result.text)
    assert result.saved_bytes == expected_saved, \
        f"saved_bytes={result.saved_bytes} != expected={expected_saved}"
    assert result.saved_bytes >= 0, f"saved_bytes is negative: {result.saved_bytes}"


def test_output_never_longer_than_input():
    """Output never longer than input."""
    text = "line1\nline2\n"
    result = optimize.optimize(text)
    assert len(result.text) <= len(text), \
        f"Output longer than input: {len(result.text)} > {len(text)}"


def test_non_str_input():
    """None and non-str input return a safe Report without raising."""
    result = optimize.optimize(None)
    assert result.text == ""
    assert result.evaluations == ()
    assert result.applied == ()
    assert result.elapsed_ms == 0.0
    assert result.saved_bytes == 0

    result2 = optimize.optimize(123)
    assert result2.text == ""
    assert result2.evaluations == ()
    assert result2.applied == ()
    assert result2.elapsed_ms == 0.0
    assert result2.saved_bytes == 0


def test_empty_string():
    """Empty string returns safe Report without raising."""
    result = optimize.optimize("")
    assert result.text == ""
    assert result.evaluations == ()
    assert result.applied == ()
    assert result.elapsed_ms == 0.0
    assert result.saved_bytes == 0


def test_min_saved_bytes_rejection():
    """A candidate smaller than min_saved_bytes is rejected."""
    text = "line1\nline2\n"
    result = optimize.optimize(text, min_saved_bytes=10**9)
    # No candidate should save 10**9 bytes, so nothing should be applied
    assert result.applied == (), f"Expected no applied with huge min_saved_bytes, got {result.applied}"
    assert result.saved_bytes == 0, f"Expected saved_bytes=0, got {result.saved_bytes}"


def test_as_dict_json():
    """Report.as_dict() has required fields and json.dumps succeeds."""
    import json
    text = "line1\nline2\n"
    result = optimize.optimize(text)
    d = result.as_dict()
    # Check required keys
    assert "passes" in d, "Missing 'passes' in as_dict"
    assert "applied" in d, "Missing 'applied' in as_dict"
    assert "saved_bytes" in d, "Missing 'saved_bytes' in as_dict"
    assert "elapsed_ms" in d, "Missing 'elapsed_ms' in as_dict"
    assert "evaluations" in d, "Missing 'evaluations' in as_dict"
    # json.dumps should succeed
    s = json.dumps(d)
    assert isinstance(s, str)


def test_candidate_smaller_than_min_saved_bytes():
    """A candidate that saves fewer bytes than min_saved_bytes is rejected."""
    text = "short"
    result = optimize.optimize(text, min_saved_bytes=10)
    # "short" is 5 bytes, any change would save at most 4 bytes
    # So nothing should be applied
    assert result.applied == (), f"Expected no applied, got {result.applied}"

def test_combination_search_finds_a_pair_after_singles_are_too_small():
    """Aggressive mode can accept a pair when neither single meets the minimum."""
    def remove_aa(text):
        return text.replace("aa", "")

    def remove_bb(text):
        return text.replace("bb", "")

    strategies = (
        optimize.Strategy("aa", remove_aa),
        optimize.Strategy("bb", remove_bb),
    )
    base = "xxaa yybb"
    result = optimize.optimize(base, strategies=strategies, min_saved_bytes=4,
                               max_passes=4, combine=True)
    assert result.saved_bytes == 4
    assert len(result.applied) == 1 and "+" in result.applied[0]


def test_combination_search_is_off_by_default_and_never_grows_text():
    """Without combine, a pair is not tried; with it, text never grows."""
    def append(text):
        return text + "x"

    strategies = (optimize.Strategy("append", append),)
    base = "line\n"
    plain = optimize.optimize(base, strategies=strategies, combine=False)
    assert plain.text == base
    combined = optimize.optimize(base, strategies=strategies, combine=True,
                                 max_passes=1)
    assert len(combined.text) <= len(base)


def test_savings_are_measured_in_utf8_bytes_not_code_points():
    """A multi-byte space replaced by an ASCII one saves a byte, not zero.

    ``len()`` sees ``hello\\u00a0world`` and ``hello world`` as the same size, so
    a code-point measurement rejected the only saving the strategy produced.
    """
    payload = "hello\u00a0world"
    assert len(payload) == len("hello world")
    report = optimize.optimize(payload, max_passes=1)
    assert report.text == "hello world"
    assert report.saved_bytes == 1


def test_a_pair_applies_each_prefix_once():
    """The pair phase applies a first strategy once, not once per second."""
    Strategy = optimize.Strategy
    calls: list[str] = []

    def counted(name: str):
        def apply(text: str) -> str:
            calls.append(name)
            # Change the text, so the combination phase opens, but make it
            # longer, so nothing is accepted and the phase is reached.
            return text + name
        return Strategy(name=name, apply=apply, note="counts its own applications")

    strategies = [counted(name) for name in ("alpha", "beta", "gamma")]
    result = optimize.optimize("line\n" * 8, max_passes=1, combine=True,
                               strategies=strategies)
    assert result.applied == ()
    # Three single applications, then three prefixes and six suffixes: twelve,
    # where re-applying every prefix per pair would take fifteen.
    assert len(calls) == 12, calls


class _UncachedApplied(optimize._Applied):
    """An ``_Applied`` whose memo never answers and never grows."""

    lookups = 0

    def _lookup(self, key):
        type(self).lookups += 1
        return None

    def _store(self, key, value):
        pass


def test_the_search_result_does_not_change_with_the_prefix_memo():
    """A cache that always misses returns the same search result.

    Only the memo access is disabled: ``chain`` still performs its base check
    and recurses through the same application path as the real cache.
    """
    text = "alpha  \r\nbeta\n\n\n\n\ngamma\ndelta\n" * 6
    # A minimum nothing can reach keeps every single rejected, so the searches
    # reach the pair and triple phases their caches serve.
    budgets = {"max_passes": 4, "combine": True, "triple": True,
               "min_saved_bytes": 10 ** 9}
    memoised = optimize.optimize(text, **budgets)

    real = optimize._Applied
    _UncachedApplied.lookups = 0
    optimize._Applied = _UncachedApplied
    try:
        recomputed = optimize.optimize(text, **budgets)
    finally:
        optimize._Applied = real

    assert memoised.text == recomputed.text
    assert memoised.applied == recomputed.applied
    assert [item.name for item in memoised.evaluations] == [
        item.name for item in recomputed.evaluations]
    assert _UncachedApplied.lookups > 0, "the uncached run never probed its memo"


def test_a_pair_accepted_before_the_triple_phase_matches_a_fresh_search():
    """A run that accepts a pair before the triple phase matches a fresh run."""
    def remove(token):
        return lambda text: text.replace(token, "")

    strategies = (
        optimize.Strategy("aa", remove("aa")),
        optimize.Strategy("bb", remove("bb")),
        optimize.Strategy("cc", remove("cc")),
    )
    text = "xx aa yy bb zz\n"
    kwargs = dict(strategies=strategies, min_saved_bytes=3, max_passes=1,
                  combine=True, triple=True)
    memoised = optimize.optimize(text, **kwargs)

    real = optimize._Applied
    optimize._Applied = _UncachedApplied
    try:
        recomputed = optimize.optimize(text, **kwargs)
    finally:
        optimize._Applied = real

    assert memoised.applied == ("aa+bb",), memoised.applied
    assert memoised.text == recomputed.text
    assert memoised.applied == recomputed.applied
    assert [item.name for item in memoised.evaluations] == [
        item.name for item in recomputed.evaluations]
    assert all(item.name.count("+") < 2 for item in memoised.evaluations)


def test_a_cache_answers_only_for_the_text_it_was_built_from():
    """A second text is computed fresh and the first text keeps its memo.

    A cache that rebound its base would lose the first text's prefix, and a
    memo keyed only by strategy names would answer the second text with text
    built from the first. Both are excluded here.
    """
    Strategy = optimize.Strategy
    seen: list[tuple[str, str]] = []

    def stage(name, transform):
        def apply(text):
            seen.append((name, text))
            return transform(text)
        return Strategy(name=name, apply=apply, note="records its input")

    strategies = (
        stage("strip", lambda text: text.replace("-", "")),
        stage("upper", str.upper),
        stage("squeeze", lambda text: text.replace(" ", "")),
    )
    first_base = "a-b"
    second_base = "c d"
    cache = optimize._Applied(first_base)
    assert cache.chain(first_base, strategies) == ("AB", "")
    assert [name for name, _ in seen] == ["strip", "upper", "squeeze"]
    seen.clear()
    # The second base must recompute every level, including the prefixes the
    # first base cached, instead of answering from the first text.
    assert cache.chain(second_base, strategies) == ("CD", "")
    assert seen == [("strip", "c d"), ("upper", "c d"), ("squeeze", "C D")]
    assert cache._base == first_base, "the cache rebound to another text"
    seen.clear()
    # The first text still answers from its own memo: the call about the other
    # text did not discard or poison it.
    assert cache.chain(first_base, strategies) == ("AB", "")
    assert seen == [], "a call with another text discarded the original memo"


def test_an_equal_copy_of_the_text_answers_from_the_cache():
    """The cache is bound to the text value, not to the string object."""
    Strategy = optimize.Strategy
    seen: list[str] = []

    def stage(name, transform):
        def apply(text):
            seen.append(name)
            return transform(text)
        return Strategy(name=name, apply=apply, note="counts its applications")

    strip = stage("strip", lambda text: text.replace("-", ""))
    base = "a-b"
    cache = optimize._Applied(base)
    assert cache.chain(base, (strip,)) == ("ab", "")
    assert seen == ["strip"]
    seen.clear()
    copy = "".join(["a-", "b"])
    assert copy == base and copy is not base
    assert cache.chain(copy, (strip,)) == ("ab", "")
    assert seen == [], "an equal copy was recomputed instead of answered"
    assert cache.chain(base, (strip,)) == ("ab", "")
    assert seen == [], "the cache stopped answering for its own text"


def test_a_triple_phase_never_follows_an_accepted_pair():
    """The shared cache is safe because an accepted pair ends the pass.

    ``optimize`` enters the triple phase only under ``if not pairs``, so a pass
    that accepted a pair never evaluates a three-strategy chain and ``current``
    cannot move between the two phases.
    """
    def remove(token):
        return lambda text: text.replace(token, "")

    strategies = (
        optimize.Strategy("aa", remove("aa")),
        optimize.Strategy("bb", remove("bb")),
        optimize.Strategy("cc", remove("cc")),
    )
    result = optimize.optimize("xx aa yy bb zz\n", strategies=strategies,
                               min_saved_bytes=3, max_passes=1,
                               combine=True, triple=True)
    assert result.applied == ("aa+bb",), result.applied
    assert all(item.name.count("+") < 2 for item in result.evaluations), [
        item.name for item in result.evaluations if item.name.count("+") > 1]


def test_the_combination_phase_is_skipped_when_no_single_changes_the_text():
    """No strategy moves the text, so no chain can: the phase is not enumerated."""
    Strategy = optimize.Strategy

    def unchanged(text: str) -> str:
        return text

    strategies = tuple(
        Strategy(name=f"noop-{index}", apply=unchanged, note="Does nothing")
        for index in range(3)
    )
    text = "line one\nline two\n"
    result = optimize.optimize(text, max_passes=1, strategies=strategies,
                               combine=True, triple=True)
    names = [item.name for item in result.evaluations]
    assert all("+" not in name for name in names), names
    skipped = [item for item in result.evaluations if item.name == "combination"]
    assert len(skipped) == 1, names
    assert "no single strategy changes this text" in skipped[0].reason
    assert result.text == text, result.text
    assert result.applied == (), result.applied


def test_a_pair_still_runs_when_a_single_rewrites_the_text_without_saving():
    """A single that changes the text keeps the phase alive even when it saves nothing."""
    Strategy = optimize.Strategy

    def grow(text: str) -> str:
        return text + "grew"

    def finish(text: str) -> str:
        return text.replace("grew", "")

    strategies = (
        Strategy(name="grow", apply=grow, note="Grows the text"),
        Strategy(name="finish", apply=finish, note="Undoes the growth"),
    )
    text = "payload\n"
    result = optimize.optimize(text, max_passes=1, strategies=strategies,
                               combine=True, triple=True)
    names = [item.name for item in result.evaluations]
    assert "grow+finish" in names, names
    assert "combination" not in names, names
    assert result.text == text, result.text


def test_a_zero_minimum_keeps_the_combination_phase():
    """With a zero minimum an unchanged candidate counts as accepted, so no skip."""
    Strategy = optimize.Strategy

    def no_text(text: str) -> None:
        return None

    strategies = tuple(
        Strategy(name=f"no-text-{index}", apply=no_text, note="Produces no text")
        for index in range(2)
    )
    text = "line one\n"
    result = optimize.optimize(text, max_passes=1, min_saved_bytes=0,
                               strategies=strategies, combine=True, triple=True)
    names = [item.name for item in result.evaluations]
    assert "no-text-0+no-text-1" in names, names
    assert "combination" not in names, names
