"""The humanized CLI output must survive a legacy console code page.

Windows terminals often report cp932 (or cp1252), which cannot encode the
box-drawing and status glyphs the summary panel uses. Printing has to degrade
to ASCII instead of raising ``UnicodeEncodeError`` out of ``sutra status``.
"""

import io

from conftest import plugin_module

ui = plugin_module("ui")


class _LegacyConsole(io.StringIO):
    """A text stream like a Windows console on a non-UTF-8 code page."""

    encoding = "cp932"


def test_summary_degrades_to_ascii_on_a_legacy_code_page(monkeypatch):
    console = _LegacyConsole()
    monkeypatch.setattr(ui.sys, "stdout", console)

    ui.print_summary(
        1, 1, 0,
        integrated_clients=["claude: configured", "codex: failed"],
        next_steps=["Run `sutra doctor` to verify the installation"],
        title="SUTRA INSTALLATION REPORT",
        tty=False,
    )

    rendered = console.getvalue()
    assert "SUTRA INSTALLATION REPORT" in rendered
    assert "claude: configured" in rendered
    assert "\u2551" not in rendered  # no box-drawing glyphs in the fallback
    rendered.encode("cp932")  # and the result really is printable there


def test_successful_summary_does_not_claim_there_are_errors(capsys):
    ui.print_summary(
        1, 0, 0,
        integrated_clients=["cursor: detected"],
        next_steps=None,
        tty=False,
    )

    rendered = capsys.readouterr().out
    assert "Action required" not in rendered
    assert "Review errors" not in rendered
    assert "No further actions required" in rendered


def test_failed_summary_keeps_the_action_required_notice(capsys):
    ui.print_summary(0, 1, 0, tty=False)

    assert "Action required" in capsys.readouterr().out


def test_emit_replaces_a_glyph_outside_the_fallback_table(monkeypatch):
    console = _LegacyConsole()
    monkeypatch.setattr(ui.sys, "stdout", console)

    ui._emit("SUTRA eval \u2014 condition comparison")

    rendered = console.getvalue()
    assert "\u2014" not in rendered
    rendered.encode("cp932")  # must be printable on the console that saw it


def test_write_errors_use_the_write_error_template():
    message = ui.format_error(
        "write-error",
        details="disk full",
        step1="free some space",
        step2="retry the install",
    )

    assert "Could not write the configuration file" in message
    assert "disk full" in message
    assert "free some space" in message


def test_unsupported_operations_point_at_a_real_command():
    message = ui.format_error("unsupported-operation", client="opencode")

    assert "sutra doctor --client opencode" in message
    assert "sutra help" not in message


def test_error_output_degrades_on_a_legacy_code_page(monkeypatch):
    console = _LegacyConsole()
    monkeypatch.setattr(ui.sys, "stderr", console)

    ui.print_error("claude install failed: RuntimeError: broken adapter", tty=False)

    rendered = console.getvalue()
    assert "claude install failed" in rendered
    rendered.encode("cp932")
