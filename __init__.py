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
  SUTRA_MAX_TOKENS          advisory token ceiling per result (default unset;
                            the estimate sizes the byte budget; invalid or
                            negative values disable the token budget)
  SUTRA_TOKENIZER           tiktoken or byte-estimate (default byte-estimate;
                            tiktoken is optional and never required)
  SUTRA_STATS_FILE              where the saving log lives (default $HERMES_HOME/cache/sutra-stats.jsonl)
  SUTRA_STATS_MAX_BYTES         rotate the log at this size (default 5 MiB)
  SUTRA_STORE                   "0" stops retaining originals (default "1")
  SUTRA_STORE_DIR               where originals live (default $HERMES_HOME/cache/sutra-store)
  SUTRA_STORE_TTL_HOURS         how long an original is kept (default 168 = 7 days)
  SUTRA_STORE_MAX_BYTES         total size of the store (default 128 MiB)
  SUTRA_STORE_MAX_ENTRY_BYTES   largest single original retained (default 32 MiB)
  SUTRA_STORE_MAX_ENTRIES       most originals retained (default 2000)
  SUTRA_RECOVERY_MAX_BYTES      response cap for sutra_restore (default 8192)
  SUTRA_POLICY                  off|balanced|aggressive session guidance (default off)
  SUTRA_REUSE                   "0" disables exact-duplicate reuse (default "1")
  SUTRA_REUSE_MIN_BYTES         smallest output eligible for reuse (default 4096)
  SUTRA_JSON_ITEMS              retired; ignored (array elision changed the payload's type)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from . import config as _config
from . import metrics, policy
from . import prompt as _prompt
from . import replay
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

logger = logging.getLogger(__name__)

__version__ = "0.6.3"

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


def _replay_store():
    """The reuse store for this call, or ``None`` when it is off or broken."""
    try:
        return replay.store_for(_config)
    except Exception:  # fail open: reuse is an optimization, never a dependency
        return None


def _replay_root(args: Any) -> str:
    """The directory the call's relative paths are read from."""
    for key in ("cwd", "workdir", "root"):
        if isinstance(args, dict):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
    return os.getcwd()


def tool_execution(*, next_call, tool_name: str | None = None, args: Any = None,
                   session_id: str | None = None, **_context: Any) -> Any:
    """The middleware frame: reuse a proven-fresh read, else run and compress.

    This is the one shipped pre-dispatch boundary, so it is the only place
    where Ponytail can skip work rather than shrink it afterwards. Reuse is
    attempted first and is deliberately narrow: a structured read-only tool, a
    named local path, an unchanged dependency stamp, the same session and an
    entry inside its TTL (``replay.py`` documents the full list). Anything
    unproven falls through to ``next_call``, which runs before any try block so
    a downstream tool error still propagates unchanged.
    """
    store = _replay_store()
    if store is not None:
        try:
            decision = store.lookup(tool_name, args, session_id=session_id,
                                    root=_replay_root(args))
        except Exception:
            decision = None
        if decision is not None and isinstance(decision, replay.Hit):
            return decision.value
    result = next_call(args)
    try:
        served = process_result(result, tool_name=tool_name, session_id=session_id)
    except Exception:  # fail open: an exotic result shape is never worth a broken call
        served = result
    if store is not None:
        try:
            store.remember(tool_name, args, served, session_id=session_id,
                           root=_replay_root(args))
        except Exception:
            pass
    return served


def request_middleware(*, request: Any = None, session_id: str | None = None,
                       **_context: Any) -> dict[str, Any] | None:
    """The ``llm_request`` frame: trim recoverable bytes from the outbound copy.

    The request the host is about to send is the only place where SUTRA can
    influence what the model reads before it reads it. The policy is
    deliberately narrow (``prompt.py`` documents it): only older ``role: "tool"``
    messages are candidates, the last few messages are never touched, and the
    stored conversation is not modified. A result that SUTRA already compressed
    carries a marker and is trimmed against that id. A host-produced result is
    retained in the store first and trimmed against the new id, so the adapter
    reaches the whole conversation rather than only SUTRA's own output; if the
    store declines the write, that message goes out byte-identical. Either way
    no byte leaves the request that no id can bring back.

    Returns ``None`` when nothing changed so the host keeps its own payload
    byte-identical, and the middleware result dict otherwise. Every mode keeps
    a head/tail preview; ``beast`` keeps a smaller one (384/192 bytes against
    1,536/768), because a result that shows nothing of itself invites the agent
    to read the original again, and that second read costs more than the
    preview it replaces.
    """
    if not isinstance(request, dict):
        return None
    try:
        cfg = _config.resolve()
        if not cfg.prompt_enabled or cfg.mode == "off":
            return None
        retain = (_prompt.host_retainer(session_id=session_id)
                  if cfg.prompt_store_host else None)
        preview = bool(cfg.prompt_preview)
        decision = _prompt.prepare(
            request,
            keep_last=cfg.prompt_keep_last,
            min_message_bytes=cfg.prompt_min_message_bytes,
            min_request_bytes=cfg.prompt_min_request_bytes,
            max_messages=cfg.prompt_max_messages,
            head_bytes=cfg.prompt_preview_head if preview else 0,
            tail_bytes=cfg.prompt_preview_tail if preview else 0,
            retain=retain,
        )
    except Exception:  # fail open: a request rewrite is never worth a broken call
        return None
    metrics.record_request_rewrite(
        decision, mode=cfg.mode, session_id=session_id or "", config=cfg)
    if not decision.changed:
        return None
    return {
        "request": decision.request,
        "source": "sutra",
        "reason": decision.reason,
    }


def api_usage(**payload: Any) -> None:
    """Observe one provider usage summary after the response arrived.

    The host reports what the provider actually billed: input and output tokens
    plus the cache-read and cache-write buckets when the provider has them.
    SUTRA only counts them — it never sets a cache flag, never rewrites a cache
    breakpoint and never asks the provider to cache anything, because a cache
    the request cannot control would be a claim rather than a measurement.

    The hook returns ``None`` and swallows every failure: a missing usage block
    must not disturb a completed call.
    """
    try:
        metrics.record_provider_usage(payload.get("usage"))
    except Exception:  # observation must never break the turn
        logger.debug("sutra: provider usage observation failed", exc_info=True)
    return None


def register(ctx) -> None:
    """Hermes plugin entry point: middleware, recovery tool, cost policy.

    Compression and recovery are registered first so an older host that lacks
    the system-prompt-section API still gets the proven behaviour. The policy
    registration is isolated: it reports its own failure and never prevents
    the other two registrations.
    """
    ctx.register_middleware("tool_execution", tool_execution)
    try:
        ctx.register_middleware("llm_request", request_middleware)
    except Exception as exc:  # an older host may not know this stage
        # Compression and recovery must survive a host without the request
        # frame, so this registration never propagates.
        logger.warning("sutra: request optimization unavailable: %s", exc)
    try:
        ctx.register_hook("post_api_request", api_usage)
    except Exception as exc:  # older hosts expose no lifecycle hooks
        logger.debug("sutra: provider usage observation unavailable: %s", exc)
    ctx.register_tool(
        name=recovery.TOOL_NAME,
        toolset="sutra",
        schema=recovery.TOOL_SCHEMA,
        handler=recovery.handle_tool_call,
        description="Retrieve the omitted part of a tool result SUTRA compressed.",
        emoji="🔎",
    )
    policy.register(ctx)


__all__ = [
    "Classification",
    "NO_COMPRESS_TOOLS",
    "Outcome",
    "Ref",
    "classify",
    "collapse_repeats",
    "compress",
    "compress_result",
    "api_usage",
    "config_from_env",
    "configuration",
    "enabled",
    "metrics_snapshot",
    "outcome_json",
    "policy",
    "process_result",
    "process_text",
    "register",
    "request_middleware",
    "restore",
    "should_bypass",
    "stats_path",
    "strip_ansi",
    "tool_execution",
    "__version__",
]
