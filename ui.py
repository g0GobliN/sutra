"""SUTRA CLI — Humanized user interface module.

Provides rich, GUI-like text output for CLI commands:
- Progress indicators with step counters and checkmarks
- Color-coded status icons (✓ ✗ ⚠ →)
- Summary panels at command completion
- Friendly, actionable error messages (no raw tracebacks)
- Inline next-step suggestions
- TTY-aware: degrades gracefully when colors are unavailable
"""

from __future__ import annotations

from typing import Sequence

import json
import os
import sys

# --- ANSI color codes --------------------------------------------------------

# When stdout is a TTY and NO_COLOR is not set, use colors.
# When piped or NO_COLOR is set, codes are stripped (see colorize()).

_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "fg_red": "\033[31m",
    "fg_green": "\033[32m",
    "fg_yellow": "\033[33m",
    "fg_blue": "\033[34m",
    "fg_cyan": "\033[36m",
    "bg_red": "\033[41m",
    "bg_yellow": "\033[43m",
}


def _is_tty() -> bool:
    """Return True if stdout is a terminal and colors should be used."""
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _has_no_color() -> bool:
    """Check if NO_COLOR env var is set."""
    return os.environ.get("NO_COLOR", "") != ""


def _has_force_color() -> bool:
    """Check if FORCE_COLOR env var is set (always use colors)."""
    return os.environ.get("FORCE_COLOR", "") != ""


# --- Console encoding fallback -------------------------------------------------
#
# Windows consoles frequently report a legacy code page (cp932, cp1252, ...)
# that cannot encode the box-drawing and status glyphs used below. Printing a
# summary must never crash the command, so every line goes through _emit(),
# which substitutes ASCII equivalents when the destination cannot encode the
# text. Values with no faithful ASCII form are replaced, never guessed at.

_ASCII_FALLBACK = str.maketrans({
    "\u2554": "+", "\u2557": "+", "\u255a": "+", "\u255d": "+",
    "\u2560": "+", "\u2563": "+", "\u2566": "+", "\u2569": "+",
    "\u2551": "|", "\u2550": "=", "\u2500": "-", "\u00b7": ".",
    "\u2713": "OK", "\u2717": "X", "\u26a0": "!", "\u2139": "i",
    "\u2192": ">", "\u27a4": ">", "\u2022": "-",
    "\u2014": "-", "\u2013": "-", "\u0394": "delta",
    "\u2705": "[ok]", "\u274c": "[x]", "\U0001f4cb": "!",
    "\U0001f4a1": ">", "\U0001f50d": "?", "\u23f1": "!",
    "\u2588": "#", "\u2591": ".",
})

_ENCODING_PROBE = "\u2713\u2551\u2550\u26a0\u2192\u2705\u27a4"
_ENCODING_CACHE: dict[str, bool] = {}


def _stream_encodes(stream) -> bool:
    """True when ``stream`` can encode the glyphs this module prints."""
    encoding = getattr(stream, "encoding", None) or ""
    cached = _ENCODING_CACHE.get(encoding)
    if cached is None:
        try:
            _ENCODING_PROBE.encode(encoding or "ascii")
            cached = True
        except (UnicodeEncodeError, LookupError):
            cached = False
        _ENCODING_CACHE[encoding] = cached
    return cached


def _render(text: str, stream) -> str:
    """Return ``text`` as ASCII when ``stream`` cannot encode it."""
    if _stream_encodes(stream):
        return text
    rendered = text.translate(_ASCII_FALLBACK)
    # The table covers the glyphs this CLI prints; a value from a config file
    # or task description can still carry something else. Printing must never
    # raise, so replace whatever the destination still cannot encode.
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        rendered.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        rendered = rendered.encode(encoding, "replace").decode(encoding)
    return rendered


def _emit(text: str = "", /, *, file=None, end: str = "\n", flush: bool = False) -> None:
    """Print through the encoding fallback."""
    stream = file if file is not None else sys.stdout
    print(_render(text, stream), end=end, file=stream, flush=flush)


def colorize(text: str, color_key: str) -> str:
    """Wrap text with ANSI color codes, respecting TTY/NO_COLOR settings.

    Args:
        text: The text to colorize.
        color_key: Key into _COLORS dict.

    Returns:
        Text wrapped with ANSI codes (or plain text if colors disabled).
    """
    is_tty = _is_tty()
    has_force = _has_force_color()
    if is_tty or has_force:
        color = _COLORS.get(color_key, "")
        return f"{color}{text}\033[0m"
    return text


def icon(
    status: str,
    /,
    *,
    tty: bool | None = None,
) -> str:
    """Return a status icon with appropriate color.

    Args:
        status: One of 'success', 'fail', 'warning', 'skipped', 'info'.
        tty: Override TTY detection. Defaults to auto-detect.

    Returns:
        Colored icon string (e.g. '\033[32m✓\033[0m' or just '✓').
    """
    tty_mode = tty if tty is not None else _is_tty()
    base = {"success": "✓", "fail": "✗", "warning": "⚠", "skipped": "→", "info": "ℹ"}[status]
    colors = {
        "success": "fg_green",
        "fail": "fg_red",
        "warning": "fg_yellow",
        "skipped": "fg_blue",
        "info": "fg_cyan",
    }
    color_key = colors.get(status, "reset")
    if tty_mode:
        return colorize(base, color_key)
    return base


# --- Step progress rendering ---------------------------------------------------


def print_step(
    step: int,
    total: int,
    status: str,
    message: str,
    /,
    *,
    tty: bool | None = None,
) -> None:
    """Render a single progress step line.

    Args:
        step: Current step number (1-indexed).
        total: Total number of steps.
        status: 'success', 'fail', 'warning', 'skipped', or 'info'.
        message: Description of what this step does.
        tty: Override TTY detection.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    icon_str = icon(status, tty=has_tty)
    step_fmt = f"Step {step}/{total}: {message} {icon_str}"
    # When not a TTY, strip ANSI and keep plain text
    if not has_tty:
        step_fmt = (
            step_fmt.replace("\033[32m", "")
            .replace("\033[31m", "")
            .replace("\033[33m", "")
            .replace("\033[34m", "")
            .replace("\033[36m", "")
            .replace("\033[0m", "")
        )
    _emit(step_fmt)


def print_progress_bar(
    current: int,
    total: int,
    /,
    *,
    label: str = "",
    width: int = 40,
    tty: bool | None = None,
) -> None:
    """Render a simple progress bar (blocks only shown on TTY).

    Args:
        current: Current step (1-indexed).
        total: Total steps.
        label: Optional label to prepend.
        width: Bar width in characters.
        tty: Override TTY detection.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    if not has_tty:
        # On non-TTY, just print a simple status line
        pct = current / total * 100
        _emit(f"\r{label} {current}/{total} ({pct:.0f}%)", end="", flush=True)
        return

    percent = current / total
    filled = int(width * percent)
    bar = "█" * filled + "░" * (width - filled)
    pct = current / total * 100
    bar_str = f"\r{label} [{bar}] {current}/{total} ({pct:.0f}%)"
    _emit(bar_str, end="", flush=True)


# --- Summary panel ------------------------------------------------------------


def print_summary(
    success: int,
    failed: int,
    skipped: int,
    /,
    *,
    integrated_clients: list[str] | None = None,
    next_steps: list[str] | None = None,
    tty: bool | None = None,
    title: str = "SUTRA INSTALLATION REPORT",
) -> None:
    """Render a summary panel at command completion.

    Args:
        success: Number of steps that succeeded.
        failed: Number of steps that failed.
        skipped: Number of steps that were skipped.
        integrated_clients: List of client names and their status.
        next_steps: List of suggested next-step messages.
        tty: Override TTY detection.
        title: Panel title.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    total = success + failed + skipped

    # Build the panel
    lines = []

    # Top border
    if has_tty:
        lines.append("╔" + "═" * 70 + "╗")
    else:
        lines.append("[" + "=" * 70 + "]")

    # Title
    if has_tty:
        title = colorize(title, "bold")
    lines.append(f"║  {title:<68} ║")

    # Separator
    if has_tty:
        lines.append("╠" + "═" * 70 + "╣")
    else:
        lines.append("[" + "-" * 70 + "]")

    # Summary counts
    count_parts = []
    if success > 0:
        count_parts.append(f"✅  Steps completed: {success}/{total}")
    if failed > 0:
        count_parts.append(f"❌  Steps failed: {failed}/{total}")
    if skipped > 0:
        count_parts.append(f"→  Steps skipped: {skipped}/{total}")
    count_line = "  ".join(count_parts)
    lines.append(f"  {count_line:<70} ║" if has_tty else f"  {count_line}")

    # Separator
    if has_tty:
        lines.append("╠" + "═" * 70 + "╣")
    else:
        lines.append("[" + "-" * 70 + "]")

    # Integrated clients
    if integrated_clients:
        for client_info in integrated_clients:
            if has_tty:
                lines.append(f"  ║  {client_info:<70} ║")
            else:
                lines.append(f"  {client_info}")

    # Separator
    if has_tty:
        lines.append("╠" + "═" * 70 + "╣")
    else:
        lines.append("[" + "-" * 70 + "]")

    # Action required: only when a step actually failed. A successful report
    # must not tell the user to review errors that do not exist.
    if failed > 0:
        action_msg = "📋  Action required: Review errors above, then run `sutra doctor`"
        if has_tty:
            lines.append(f"  ║  {action_msg:<70} ║")
        else:
            lines.append(f"  {action_msg}")

        # Separator
        if has_tty:
            lines.append("╠" + "═" * 70 + "╣")
        else:
            lines.append("[" + "-" * 70 + "]")

    # Next steps
    if next_steps:
        if has_tty:
            lines.append("  ║")
        for i, step in enumerate(next_steps):
            prefix = "  ║    " if has_tty else "  "
            if has_tty:
                colored_step = colorize(step, "fg_cyan")
                lines.append(f"{prefix}➤  {colored_step}")
            else:
                lines.append(f"{prefix}➤  {step}")
    else:
        if has_tty:
            lines.append("  ║    ➤  No further actions required.")
        else:
            lines.append("  ➤  No further actions required.")

    # Separator
    if has_tty:
        lines.append("╠" + "═" * 70 + "╣")
    else:
        lines.append("[" + "-" * 70 + "]")

    # Bottom status
    if total == 0:
        status_line = "→  No steps to run"
    elif success == total and failed == 0 and skipped == 0:
        status_line = "✅  All steps completed successfully"
    elif failed == 0 and skipped > 0:
        status_line = (f"→  Completed: {success} succeeded, "
                       f"{skipped} skipped")
    elif failed == 0:
        status_line = f"→  All steps completed ({success}/{total} steps)"
    elif skipped == 0:
        status_line = f"⚠  Installation completed with {failed} error(s)"
    else:
        status_line = f"⚠  Installation completed with {failed} error(s) and {skipped} skipped"

    if has_tty:
        lines.append(f"  {status_line}")
    else:
        lines.append(status_line)

    # Bottom border
    if has_tty:
        lines.append("╚" + "═" * 70 + "╝")
    else:
        lines.append("[" + "=" * 70 + "]")

    # Print all lines
    for line in lines:
        _emit(line)


# --- Friendly error messaging -------------------------------------------------


_ERROR_TEMPLATES = {
    "permission-error": (
        "🔍 What happened: {details}\n"
        "💡 Suggested next steps:\n"
        "  • {step1}\n"
        "  • {step2}"
    ),
    "not-installed": (
        "⚠️  {client} is not installed.\n"
        "💡 Install {client} first, or skip with `--skip-{client}`."
    ),
    "corrupt-config": (
        "⚠️  Configuration file appears to be corrupted or contains invalid JSON.\n"
        "📋 A backup was created at {backup_path}.\n"
        "💡 To restore from backup: `sutra restore --id {backup_id}`."
    ),
    "client-not-configured": (
        "⚠️  {client} was detected but not fully configured.\n"
        "💡 Run `sutra config` to complete the setup for {client}."
    ),
    "timeout": (
        "⏱  The operation timed out.\n"
        "💡 The CLI may be unresponsive. Try again or check your connection."
    ),
    "unsupported-operation": (
        "⚠️  The requested operation is not supported for {client}.\n"
        "💡 Run `sutra doctor --client {client}` to see what is supported and configured."
    ),
    "write-error": (
        "⚠️  Could not write the configuration file.\n"
        "📋 Details: {details}\n"
        "💡 Suggested next steps:\n"
        "  • {step1}\n"
        "  • {step2}"
    ),
}


def format_error(
    category: str,
    /,
    *,
    details: str = "",
    client: str | None = None,
    backup_path: str | None = None,
    backup_id: str | None = None,
    step1: str | None = None,
    step2: str | None = None,
) -> str:
    """Return a friendly, actionable error message for the given category.

    Replaces raw Python tracebacks with user-friendly messages that suggest
    concrete next steps.

    Args:
        category: Error category key (matches _ERROR_TEMPLATES).
        details: Additional details to include in the message.
        client: Client name (e.g. 'opencode', 'claude', 'codex').
        backup_path: Path to backup file.
        backup_id: Backup ID for restore.
        step1: First suggested step.
        step2: Second suggested step.

    Returns:
        Formatted error string ready for display.
    """
    template = _ERROR_TEMPLATES.get(category, "⚠️  An unexpected error occurred.")

    # Fill in template placeholders
    if category == "permission-error":
        filled_details = details or "The operation was denied permission."
        s1 = step1 or "Run `sutra doctor` to diagnose the issue"
        s2 = step2 or "Change file permissions if the path is known"
        return template.format(
            details=filled_details,
            step1=s1,
            step2=s2,
        )

    if category == "not-installed":
        c = client or "the requested client"
        return template.format(client=c)

    if category == "corrupt-config":
        return template.format(
            backup_path=backup_path or "a backup file",
            backup_id=backup_id or "the backup ID",
        )

    if category == "client-not-configured":
        c = client or "the client"
        return template.format(client=c)

    if category == "timeout":
        return template

    if category == "unsupported-operation":
        c = client or "the client"
        return template.format(client=c)

    if category == "write-error":
        return template.format(
            details=details or "the configuration file could not be written",
            step1=step1 or "Run `sutra doctor` to diagnose",
            step2=step2 or "Check file permissions and free disk space",
        )

    # Fallback: return details if we have them, otherwise a generic message
    if details:
        return f"⚠️  Error: {details}\n💡 Run `sutra doctor` to diagnose."
    return "⚠️  An unexpected error occurred. Run `sutra doctor` to diagnose."


# --- Output control helpers ----------------------------------------------------


def print_success(message: str, /, *, tty: bool | None = None) -> None:
    """Print a success message with green checkmark.

    Args:
        message: The success message.
        tty: Override TTY detection.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    icon_str = icon("success", tty=has_tty)
    msg = f"{icon_str}  {message}"
    if has_tty:
        msg = colorize(msg, "fg_green")
    _emit(msg)


def print_warning(message: str, /, *, tty: bool | None = None) -> None:
    """Print a warning message with yellow triangle.

    Args:
        message: The warning message.
        tty: Override TTY detection.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    icon_str = icon("warning", tty=has_tty)
    msg = f"{icon_str}  {message}"
    if has_tty:
        msg = colorize(msg, "fg_yellow")
    _emit(msg)


def print_info(message: str, /, *, tty: bool | None = None) -> None:
    """Print an informational message with blue formatting.

    Args:
        message: The info message.
        tty: Override TTY detection.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    if has_tty:
        msg = colorize(message, "fg_cyan")
    else:
        msg = message
    _emit(msg)


def print_error(message: str, /, *, tty: bool | None = None) -> None:
    """Print an error message with red checkmark.

    Args:
        message: The error message.
        tty: Override TTY detection.
    """
    if _json_env():
        return
    has_tty = tty if tty is not None else _is_tty()
    icon_str = icon("fail", tty=has_tty)
    msg = f"{icon_str}  {message}"
    if has_tty:
        msg = colorize(msg, "fg_red")
    _emit(msg, file=sys.stderr)


# --- Environment-aware output: JSON mode --------------------------------------


def emit_json(
    *,
    step: int | None = None,
    total: int | None = None,
    status: str | None = None,
    message: str | None = None,
    success: int | None = None,
    failed: int | None = None,
    skipped: int | None = None,
    integrated_clients: Sequence[object] | None = None,
    next_steps: list[str] | None = None,
    error: dict | None = None,
    **extra: object,
) -> None:
    """Emit structured JSON output when SUTRA_JSON=1 is set.

    This allows programmatic consumption of SUTRA output while the human-
    readable UI is shown on stdout when running interactively.

    Args:
        step: Current step number.
        total: Total steps.
        status: Step status.
        message: Step message.
        success: Success count.
        failed: Failed count.
        skipped: Skipped count.
        integrated_clients: Client status info.
        next_steps: Next step suggestions.
        error: Error dict with category and message.
        extra: Additional fields merged into the emitted object.
    """
    data: dict[str, object] = {}

    if step is not None:
        data["step"] = step
    if total is not None:
        data["total"] = total
    if status is not None:
        data["status"] = status
    if message is not None:
        data["message"] = message
    if success is not None:
        data["success"] = success
    if failed is not None:
        data["failed"] = failed
    if skipped is not None:
        data["skipped"] = skipped
    if integrated_clients is not None:
        data["integrated_clients"] = integrated_clients
    if next_steps is not None:
        data["next_steps"] = next_steps
    if error is not None:
        data["error"] = error
    for key, value in extra.items():
        data[key] = value

    output = json.dumps(data, ensure_ascii=False)
    # JSON must stay valid: escape non-ASCII rather than substituting glyphs.
    if not _stream_encodes(sys.stdout):
        output = json.dumps(data, ensure_ascii=True)
    print(output)


def _should_emit_json() -> bool:
    """True only when JSON output was explicitly requested.

    Being piped is not a request for machine output: the adapters write human
    progress to stdout as well, which would corrupt a JSON stream. The CLI's
    documented --json flag and SUTRA_JSON=1 are the explicit selectors.
    """
    return _json_env()


def _json_env() -> bool:
    """Check if SUTRA_JSON=1 was explicitly set (structured JSON requested)."""
    return os.environ.get("SUTRA_JSON", "") == "1"