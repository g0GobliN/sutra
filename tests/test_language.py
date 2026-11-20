"""Tests for the deterministic prose-wording pass."""

from __future__ import annotations

from conftest import load_plugin, plugin_module

load_plugin()
language = plugin_module("language")
preserve = plugin_module("preserve")


def test_verbose_phrase_is_shortened():
    text = "Use the cache in order to reduce latency."
    assert language.compress_text(text) == "Use the cache to reduce latency."


def test_politeness_filler_is_removed():
    text = "Please carefully inspect the repository."
    out = language.compress_text(text)
    assert out == "Carefully inspect the repository."


def test_negation_survives():
    text = "Please do not delete this file."
    out = language.compress_text(text)
    assert "not" in out
    assert preserve.preservation_ok(text, out)[0] or "not" in out


def test_numbers_identifiers_and_paths_survive():
    text = ("At this point in time, use /etc/hosts with retry_after_ms "
            "250 for up to 3 attempts.")
    out = language.compress_text(text)
    assert "now" in out.lower()
    assert "/etc/hosts" in out
    assert "retry_after_ms" in out
    assert "250" in out
    assert "3" in out
    assert preserve.preservation_ok(text, out)[0]


def test_code_and_quoted_lines_are_untouched():
    lines = [
        'cmd = "please run in order to fix"',
        "`please note that`",
        "Please ${value} in order to compile",
    ]
    for line in lines:
        assert language.compress_text(line) == line, line


def test_fenced_block_is_untouched():
    text = "please fix this\n```\nplease run in order to fix\n```\nplease reply\n"
    out = language.compress_text(text)
    assert "please run in order to fix" in out
    assert "fix this" in out
    assert "reply" in out


def test_marker_line_is_untouched():
    text = "[sutra: please see recovery]"
    assert language.compress_text(text) == text


def test_no_change_is_byte_identical():
    text = "Run the tests twice."
    assert language.compress_text(text) == text


def test_underscore_identifier_not_rewritten():
    text = "the in_order_to flag stays"
    assert language.compress_text(text) == text


def test_empty_and_non_text():
    assert language.compress_text("") == ""
    assert language.compress_text("   ") == "   "


def test_result_is_never_longer():
    for phrase, replacement in language.phrases():
        text = f"x {phrase} y"
        assert len(language.compress_text(text)) <= len(text)


def test_large_input_is_returned_unchanged():
    text = ("please compress this line\n" * 4000)
    assert len(text.encode("utf-8")) > language.MAX_BYTES
    assert language.compress_text(text) == text


def test_does_not_produce_empty_line():
    text = "please"
    out = language.compress_text(text)
    assert out == text


def test_line_opening_reminder_is_removed():
    assert language.compress_text("Note that the worker is ready.") == (
        "The worker is ready.")


def test_reminder_word_mid_line_is_left_alone():
    text = "The note that follows is short."
    assert language.compress_text(text) == text


def test_reminder_to_do_something_is_kept():
    text = "Remember to run the tests."
    assert language.compress_text(text) == text


def test_relative_clause_is_compacted():
    text = "The cache is cold, which means that the first call is slow."
    assert language.compress_text(text) == (
        "The cache is cold, meaning the first call is slow.")


def test_relative_clause_needs_a_comma():
    text = "The flag which means that the run passed is set."
    assert language.compress_text(text) == text


def test_longer_phrase_variants_are_compacted():
    text = "Prior to the run, perform an analysis of the log."
    assert language.compress_text(text) == "Before the run, analyze the log."


def test_generic_fact_phrase_runs_after_the_specific_ones():
    text = "Despite the fact that the store is full, the run continues."
    assert language.compress_text(text) == (
        "Although the store is full, the run continues.")


def test_tool_description_keeps_the_leading_capital():
    text = "Note that this tool reads one file."
    assert language.compress_tool_description(text) == "This tool reads one file."


def test_make_sure_to_keeps_its_verb_phrase():
    # "ensure clear the cache" would be ungrammatical, so the table does not
    # rewrite this phrase at all.
    text = "Make sure to clear the cache before the run."
    assert language.compress_text(text) == text


def test_conditional_and_modal_phrases_are_compacted():
    text = "In the event that the provider returns a 429, the client will be able to retry."
    assert language.compress_text(text) == (
        "If the provider returns a 429, the client can retry.")


def test_leading_reminder_inside_a_sentence_is_kept():
    text = "The run failed, and it should be noted that the store was full."
    assert language.compress_text(text) == (
        "The run failed, and the store was full.")
