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

An installed package exposes the same file as the ``sutra-hook`` command, which
is the path a pip-installed user should give Claude Code.

Recovery is the existing MCP server (``sutra_restore``); point the hook and the
MCP server at the same ``SUTRA_STORE_DIR`` (or ``HERMES_HOME``).
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import sys

from _sutra import load_sutra

# The engine refuses to rewrite above 64 MiB. The envelope adds little; a
# larger input is left alone rather than read without bound.
MAX_INPUT_BYTES = 72 * 1024 * 1024
MAX_PERSISTED_BYTES = 64 * 1024 * 1024

# Claude Code persists large successful Bash/PowerShell output itself and
# gives the hook only the first ``bashOutputMaxChars`` (30,000 by default) in
# ``tool_response.stdout``. The full text is in ``persistedOutputPath``. A hook
# that compressed the preview would store a truncated "original" and hand the
# model an id that cannot restore what the tool actually printed, so a result
# with persisted metadata is either read in full or left untouched.
PERSISTED_TOOLS = frozenset({"Bash", "PowerShell"})


def _persisted_text(payload: dict, response: dict) -> str | None:
    """The full persisted stdout, or None when it cannot be trusted.

    Only the host's own Bash/PowerShell persistence is accepted: a regular
    (non-symlink) ``.txt`` file named by ``persistedOutputPath``, whose size
    matches ``persistedOutputSize``, under the session's ``tool-results``
    directory. A tool result can carry arbitrary JSON, so treating any path in
    it as readable would turn the hook into a file-disclosure primitive.
    """
    if payload.get("tool_name") not in PERSISTED_TOOLS:
        return None
    raw_path = response.get("persistedOutputPath")
    declared = response.get("persistedOutputSize")
    transcript = payload.get("transcript_path")
    if (not isinstance(raw_path, str) or not raw_path
            or not isinstance(declared, int) or declared <= 0
            or declared > MAX_PERSISTED_BYTES
            or not isinstance(transcript, str) or not transcript):
        return None
    path = pathlib.Path(raw_path)
    try:
        if (not path.is_absolute() or path.suffix != ".txt"
                or path.parent.name != "tool-results"):
            return None
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        root = pathlib.Path(transcript).parent.resolve(strict=True)
        path.resolve(strict=True).relative_to(root)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if os.fstat(fd).st_size != declared:
                return None
            data = os.read(fd, declared + 1)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None
    if len(data) != declared:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


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
        if isinstance(original, dict) and original.get("persistedOutputPath"):
            full = _persisted_text(payload, original)
            if full is None:
                return _noop()  # never store the host's truncated preview
            original = {key: value for key, value in original.items()
                        if key not in ("persistedOutputPath", "persistedOutputSize")}
            original["stdout"] = full
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
