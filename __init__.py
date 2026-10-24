"""SUTRA — deterministic compression of oversized tool output, with recovery.

Hermes spends most of its tokens re-sending context on every turn, and tool
output is the part that grows without bound. This plugin sits on the
``tool_execution`` middleware path and shrinks a tool result *after* it has run,
before the model ever sees it.

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

import logging
import time
from typing import Any

from . import config as _config
from . import engine
from . import metrics
from . import recovery
from . import store as _store
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

logger = logging.getLogger(__name__)

__version__ = "0.2.0"

_TEXT_KEYS = ("output", "stdout", "stderr", "content", "text", "result", "data")
# Reading evidence is the one result that must never be compressed again.
_NO_COMPRESS_TOOLS = frozenset({recovery.TOOL_NAME})


def configuration() -> _config.Config:
    """The resolved configuration every component reads from."""
    return _config.resolve()


def enabled() -> bool:
    return _config.resolve().enabled


def stats_path() -> str:
    return _config.resolve().stats_path


def _record_compression(stats: dict[str, Any], cfg: _config.Config) -> None:
    """Best-effort hand-off to the one telemetry owner."""
    try:
        metrics.record_compression(stats, cfg)
    except Exception:  # a metric must never change the tool result
        logger.debug("sutra: compression metric failed", exc_info=True)


def _process_text(text: str, *, cfg: _config.Config, tool_name: str = "",
                  session_id: str = "") -> tuple[str, dict[str, Any]]:
    """Compress one string, retaining the original first when it is rewritten."""
    started = time.perf_counter()
    out, stats = engine.compress(text, cfg=cfg)
    stats.setdefault("stored", False)
    stats.setdefault("id", None)
    if stats.get("changed"):
        store_cfg = _store.StoreConfig.from_config(cfg)
        if cfg.store_enabled and stats["original_bytes"] <= cfg.store_max_entry_bytes:
            store = _store.Store(store_cfg)
            entry = store.put(text, tool_name=tool_name, session_id=session_id)
            if entry is not None:
                # Second pass only when the original is actually on disk, so the
                # marker can quote a real id instead of promising one.
                kept_out, kept_stats = engine.compress(
                    text, ref=Ref(entry.id, cfg.store_ttl_hours), cfg=cfg)
                if kept_stats.get("changed"):
                    out, stats = kept_out, kept_stats
                    stats["stored"] = True
                    stats["id"] = entry.id
                else:
                    store.delete(entry.id)
    stats["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    stats["tool"] = str(tool_name or "")[:64]
    return out, stats


def compress_result(result: Any, *, tool_name: str | None = None,
                    session_id: str | None = None) -> Any:
    """Compress a tool result of any common shape without changing its type.

    Never raises: an exotic result shape is never worth a broken tool call.
    """
    cfg = _config.resolve()
    if not cfg.enabled:
        return result
    try:
        if isinstance(result, str):
            out, stats = _process_text(result, cfg=cfg, tool_name=tool_name or "",
                                       session_id=session_id or "")
            _record_compression(stats, cfg)
            return out
        if isinstance(result, dict):
            changed = False
            new = dict(result)
            for key in _TEXT_KEYS:
                value = new.get(key)
                if isinstance(value, str):
                    out, stats = _process_text(value, cfg=cfg, tool_name=tool_name or "",
                                               session_id=session_id or "")
                    _record_compression(stats, cfg)
                    if stats["changed"]:
                        new[key] = out
                        changed = True
            return new if changed else result
    except Exception:  # fail open, and say so in the log rather than in the result
        logger.exception("sutra: compression failed; returning the original result")
        return result
    return result


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
    """The middleware frame: run the tool, then compress what comes back."""
    result = next_call(args)
    if tool_name in _NO_COMPRESS_TOOLS:
        return result
    try:
        return compress_result(result, tool_name=tool_name, session_id=session_id)
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
    "Ref",
    "classify",
    "collapse_repeats",
    "compress",
    "compress_result",
    "config_from_env",
    "configuration",
    "enabled",
    "metrics_snapshot",
    "register",
    "stats_path",
    "strip_ansi",
    "tool_execution",
    "__version__",
]
