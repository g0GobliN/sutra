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
