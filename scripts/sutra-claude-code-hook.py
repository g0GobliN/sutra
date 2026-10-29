#!/usr/bin/env python3
"""Claude Code PostToolUse hook: replace a tool result before the model sees it.

Claude Code runs a shell hook with the event JSON on stdin and reads a JSON
object from stdout. A ``PostToolUse`` hook may return
``hookSpecificOutput.updatedToolOutput``, which replaces the tool result for
every tool (the older ``updatedMCPToolOutput`` is MCP-only). This script is the
host adapter: it extracts ``tool_response``, hands it to SUTRA's shared seam,
and writes the returned value back into that field.

The seam (``core.process_result``) owns eligibility, size limits, format
protection, retention, metrics and the recovery-tool bypass. This file only
translates the host shape, and it never raises into the agent loop: any parse,
import or internal error prints ``{}`` and exits 0, leaving the original result
untouched.

Install (project or user settings; the command must be absolute):

    {
      "hooks": {
        "PostToolUse": [
          {"hooks": [{"type": "command",
                      "command": "python3 /path/to/sutra/scripts/sutra-claude-code-hook.py"}]}
        ]
      }
    }

Recovery is the existing MCP server (``sutra_restore``); point the hook and the
MCP server at the same ``SUTRA_STORE_DIR`` (or ``HERMES_HOME``).
"""

from __future__ import annotations

import json
import sys

from _sutra import load_sutra

# The engine refuses to rewrite above 64 MiB. The envelope adds little; a
# larger input is left alone rather than read without bound.
MAX_INPUT_BYTES = 72 * 1024 * 1024


def _noop() -> int:
    """The host's explicit 'no change' response."""
    sys.stdout.write("{}\n")
    return 0


def _read_payload() -> object:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"hook input exceeds {MAX_INPUT_BYTES:,} bytes")
    return json.loads(data.decode("utf-8", "replace"))


def main() -> int:
    try:
        sutra = load_sutra()
        payload = _read_payload()
        if not isinstance(payload, dict) or "tool_response" not in payload:
            return _noop()
        tool_name = payload.get("tool_name")
        tool_name = tool_name if isinstance(tool_name, str) else ""
        session_id = payload.get("session_id")
        session_id = session_id if isinstance(session_id, str) else ""
        original = payload["tool_response"]
        updated = sutra.core.process_result(
            original, tool_name=tool_name, session_id=session_id)
        if updated == original:
            return _noop()
        response = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": updated,
            }
        }
        # Serialize before writing: a partial write would corrupt the hook's
        # only protocol channel.
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    except Exception:  # fail open: never break a tool call over compression
        return _noop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
