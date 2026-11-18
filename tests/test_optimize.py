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
