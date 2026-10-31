"""SUTRA — deterministic compression of oversized tool output, with recovery.

Hermes spends most of its tokens re-sending context on every turn, and tool
output is the part that grows without bound. This plugin sits on the
``tool_execution`` middleware path and shrinks a tool result *after* it has run,
before the model ever sees it.

The compression itself lives in :mod:`core`, the host-agnostic seam shared with
the Claude Code hook, the CLI and the MCP server. This module is the Hermes
adapter: it registers the middleware and the recovery tool, and delegates every
decision below that to the seam.

What makes the shrinking safe is that the original is kept: every rewrite
quotes an id the agent can hand back to ``sutra_restore`` and get the exact
bytes that were left out. Correctness first, compression second.

Design rules, in priority order:

1. **Never invent content.** Only remove, collapse or elide, and always leave a
   marker saying what was removed, how much, and how to get it back.
2. **Never change a payload's own format.** Diffs are returned byte-identical,
   a JSON array stays an array, a traceback keeps its error end.
3. **Fail open.** Any unexpected shape, parse error or exception returns the
   original result untouched.
4. **Measurable.** Every decision is counted, every rewrite appends a line to a
   local savings log, and every retained original is a file the agent can ask
   for by id.

Configuration comes from the environment so it can be tuned without a code
change:

  SUTRA_ENABLED                 "0" disables compression entirely (default "1")
  SUTRA_MAX_BYTES               only results larger than this are rewritten (default 24576)
  SUTRA_HEAD_BYTES              bytes kept from the start (default 12288)
  SUTRA_TAIL_BYTES              bytes kept from the end (default 6144)
  SUTRA_STATS_FILE              where the saving log lives (default $HERMES_HOME/cache/sutra-stats.jsonl)
  SUTRA_STATS_MAX_BYTES         rotate the log at this size (default 5 MiB)
  SUTRA_STORE                   "0" stops retaining originals (default "1")
  SUTRA_STORE_DIR               where originals live (default $HERMES_HOME/cache/sutra-store)
  SUTRA_STORE_TTL_HOURS         how long an original is kept (default 168 = 7 days)
  SUTRA_STORE_MAX_BYTES         total size of the store (default 128 MiB)
  SUTRA_STORE_MAX_ENTRY_BYTES   largest single original retained (default 32 MiB)
  SUTRA_STORE_MAX_ENTRIES       most originals retained (default 2000)
  SUTRA_RECOVERY_MAX_BYTES      response cap for sutra_restore (default 8192)
  SUTRA_JSON_ITEMS              retired; ignored (array elision changed the payload's type)
"""

from __future__ import annotations

from typing import Any

from . import config as _config
from . import metrics
from . import recovery
from . import store as _store
from .core import (
    NO_COMPRESS_TOOLS,
    Outcome,
    outcome_json,
    process_result,
    process_text,
    restore,
    should_bypass,
)
from .engine import (  # re-exported: this was a single-module plugin before 0.2.0
    _DEFAULT_MAX_BYTES as _DEFAULT_MAX_BYTES,
    Classification,
    Ref,
    classify,
    collapse_repeats,
    compress,
    config_from_env,
    strip_ansi,
)

__version__ = "0.4.0"

# Backward-compatible name: before the seam extraction this was the plugin's
# own function. It is now the seam's result-shaped entry point.
compress_result = process_result


def configuration() -> _config.Config:
    """The resolved configuration every component reads from."""
    return _config.resolve()


def enabled() -> bool:
    return _config.resolve().enabled


def stats_path() -> str:
    return _config.resolve().stats_path


def metrics_snapshot(store=None) -> dict[str, Any]:
    """One surface for compression, restores, cleanup, and the store's size."""
    if store is None:
        try:
            store = _store.Store(_store.StoreConfig.from_config(_config.resolve()))
        except Exception:
            store = None
    return metrics.snapshot(store)


def tool_execution(*, next_call, tool_name: str | None = None, args: Any = None,
                   session_id: str | None = None, **_context: Any) -> Any:
    """The middleware frame: run the tool, then compress what comes back.

    The seam owns the bypass list, so a host never needs its own copy of it.
    ``next_call`` runs before the try: a downstream tool error must propagate
    unchanged, not be mistaken for a compression failure.
    """
    result = next_call(args)
    try:
        return process_result(result, tool_name=tool_name, session_id=session_id)
    except Exception:  # fail open: an exotic result shape is never worth a broken call
        return result


def register(ctx) -> None:
    """Hermes plugin entry point: middleware plus the recovery tool."""
    ctx.register_middleware("tool_execution", tool_execution)
    ctx.register_tool(
        name=recovery.TOOL_NAME,
        toolset="sutra",
        schema=recovery.TOOL_SCHEMA,
        handler=recovery.handle_tool_call,
        description="Retrieve the omitted part of a tool result SUTRA compressed.",
        emoji="🔎",
    )


__all__ = [
    "Classification",
    "NO_COMPRESS_TOOLS",
    "Outcome",
    "Ref",
    "classify",
    "collapse_repeats",
    "compress",
    "compress_result",
    "config_from_env",
    "configuration",
    "enabled",
    "metrics_snapshot",
    "outcome_json",
    "process_result",
    "process_text",
    "register",
    "restore",
    "should_bypass",
    "stats_path",
    "strip_ansi",
    "tool_execution",
    "__version__",
]
