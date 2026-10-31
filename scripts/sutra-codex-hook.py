#!/usr/bin/env python3
"""Codex CLI PostToolUse hook: replace a tool result before the model sees it.

Codex runs a ``PostToolUse`` command hook with the event JSON on stdin. A hook
that returns ``continue: false`` with ``stopReason`` replaces the tool result
for the model and continues the turn from that message. Codex marks the hook run
``Stopped`` rather than ``Blocked``; the model receives only the replacement.

This script is the host adapter. It accepts the Bash/exec result, hands the text
to SUTRA's shared seam, and returns the compressed copy with its recovery
marker. The seam (``core.process_text``) owns eligibility, size limits, format
protection, retention, metrics and the evidence-tool bypass.

It never raises into the agent loop: a parse, import or internal error prints
``{}`` and exits 0, leaving the original result untouched. Codex truncates very
large tool output before the hook, so the retained original is the text Codex
actually supplied; the marker describes that text, not output the hook never saw.

Install (the installer writes this; the command must be absolute and quoted):

    {
      "hooks": {
        "PostToolUse": [{
          "matcher": "Bash|exec_command",
          "hooks": [{"type": "command",
                     "command": "\"<python>\" \"<site-packages>/sutra/scripts/sutra-codex-hook.py\""}]
        }]
      }
    }

Codex requires a one-time trust review for a user hook: run ``/hooks`` and trust
the SUTRA entry. Recovery is the existing MCP server (``sutra_restore``); the
hook and the MCP server must share ``SUTRA_STORE_DIR`` (or ``HERMES_HOME``).
"""

from __future__ import annotations

import dataclasses
import json
import sys

from _sutra import load_sutra

# The engine refuses to rewrite above 64 MiB; the envelope adds little. A larger
# event is left alone rather than read without bound.
MAX_INPUT_BYTES = 72 * 1024 * 1024
SHELL_TOOLS = frozenset({"Bash", "exec_command"})

# Codex truncates a large tool result before the hook sees it: the payload is
# roughly 10 KB with a "Warning: truncated output" header, below the 24,576-byte
# default threshold. These caps make the host-truncated result eligible without
# changing the user's SUTRA_* settings, and only tighten them further when the
# user configured something smaller. The retained original is exactly the text
# Codex supplied; output Codex already discarded is not recoverable.
CODEX_MAX_BYTES = 8_192
CODEX_HEAD_BYTES = 3_072
CODEX_TAIL_BYTES = 1_536


def _noop() -> int:
    """The host's explicit 'no change' response."""
    sys.stdout.write("{}\n")
    return 0


def _read_payload() -> object:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"hook input exceeds {MAX_INPUT_BYTES:,} bytes")
    return json.loads(data.decode("utf-8"))


def main() -> int:
    try:
        sutra = load_sutra()
        payload = _read_payload()
        if not isinstance(payload, dict):
            return _noop()
        if payload.get("hook_event_name") not in (None, "PostToolUse"):
            return _noop()
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or tool_name not in SHELL_TOOLS:
            return _noop()
        original = payload.get("tool_response")
        if not isinstance(original, str) or not original:
            return _noop()
        session_id = payload.get("session_id")
        session_id = session_id if isinstance(session_id, str) else ""
        cfg = sutra.config.resolve()
        cfg = dataclasses.replace(
            cfg,
            max_bytes=min(cfg.max_bytes, CODEX_MAX_BYTES),
            head_bytes=min(cfg.head_bytes, CODEX_HEAD_BYTES),
            tail_bytes=min(cfg.tail_bytes, CODEX_TAIL_BYTES),
        )
        outcome = sutra.core.process_text(
            original, cfg=cfg, tool_name=tool_name, session_id=session_id)
        if not outcome.changed or not outcome.marker:
            return _noop()
        # Serialize before writing: a partial write would corrupt the hook's
        # only protocol channel. ASCII escapes keep a lone surrogate in an
        # untouched field representable on the wire instead of raising.
        sys.stdout.write(json.dumps(
            {"continue": False, "stopReason": outcome.output},
            ensure_ascii=True) + "\n")
    except Exception:  # fail open: never break a tool call over compression
        return _noop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
