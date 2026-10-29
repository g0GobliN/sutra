"""The `sutra-hook` console command: the Claude Code PostToolUse adapter.

The implementation ships as ``scripts/sutra-claude-code-hook.py``, so a
checkout and an installed wheel run the same hook. Without this command the
hook was reachable only by absolute path into a clone; with it, an installed
user points Claude Code at the console script. ``runner`` owns the handover,
as it does for ``sutra`` and ``sutra-mcp``.
"""

from __future__ import annotations

import sys

from . import runner


def main() -> int:
    return runner.run_script(
        "sutra-claude-code-hook.py", sys.argv[1:],
        missing="sutra-hook: the hook script is missing from this installation")


if __name__ == "__main__":
    raise SystemExit(main())
