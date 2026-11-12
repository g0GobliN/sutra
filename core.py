"""The host-agnostic adapter seam.

Every host integration — the Hermes middleware, the Claude Code hook, the CLI
and the MCP server — calls this module and nothing below it. A host adapter's
only job is to turn its native tool result into ``process_result`` and write
the returned value back into the field the host uses. Eligibility, size limits,
format protection, retention, metrics and recovery stay here and in the
engine/store/recovery modules they own.

One operation, one outcome: ``process_text`` returns an ``Outcome`` carrying the
rewritten text, whether it changed, whether a marker is present, and the
recovery id when one was actually retained. Adapters serialize that outcome;
none of them re-parse the marker text to reconstruct it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from . import config as _config
from . import engine, metrics, recovery, reuse as _reuse
from . import store as _store
from .engine import CompressionStats, Limits, Ref

logger = logging.getLogger(__name__)

# Only top-level string fields of a dict result are candidates. A nested
# structure or an unknown key is never rewritten.
TEXT_KEYS = ("output", "stdout", "stderr", "content", "text", "result", "data")
# Reading or producing SUTRA evidence must never be compressed again: a second
# pass would hide the bytes the agent just asked for and mint a new id.
NO_COMPRESS_TOOLS = frozenset({recovery.TOOL_NAME, "sutra_compress"})


@dataclass(frozen=True)
class Outcome:
    """What one compression decision produced, in the shared contract shape."""

    changed: bool
    output: str
    marker: bool
    id: str | None
    original_bytes: int
    compressed_bytes: int


def should_bypass(tool_name: str | None) -> bool:
    """True for the evidence tools a host must pass through untouched.

    MCP clients namespace tools as ``mcp__<server>__<tool>``; a suffix match
    keeps the bypass working without every adapter hard-coding its own names.
    """
    name = str(tool_name or "").strip()
    if not name:
        return False
    return name in NO_COMPRESS_TOOLS or any(
        name.endswith("__" + tool) for tool in NO_COMPRESS_TOOLS)


@dataclass(frozen=True)
class Contract:
    """Whether one compressed result fits its budget, stated explicitly.

    ``satisfied`` is False when the compressed text still exceeds the byte
    budget, or when the estimated tokens exceed the token budget.
    ``exceeded_by`` is how many bytes over the byte budget it is (zero when
    it fits). Hosts call ``budget_contract`` to explain *why* a result was
    kept or elided; it never changes the result.
    """

    limits: Limits
    satisfied: bool
    exceeded_by: int = 0
    token_estimate: int | None = None
    token_budget: int | None = None
    tokens_exceeded: bool = False


def budget_contract(*, original_bytes: int, compressed_bytes: int,
                    limits: Limits, token_estimate: int | None = None,
                    token_budget: int | None = None) -> Contract:
    """Report whether a result fits its budget, explicitly.

    A host passes the engine's stats and the resolved limits; the result is
    the contract verdict, not a new transformation.
    """
    byte_ok = compressed_bytes <= limits.max_bytes
    exceeded_by = max(0, compressed_bytes - limits.max_bytes)
    tokens_exceeded = (token_budget is not None
                       and token_estimate is not None
                       and token_estimate > token_budget)
    return Contract(
        limits=limits,
        satisfied=byte_ok and not tokens_exceeded,
        exceeded_by=exceeded_by,
        token_estimate=token_estimate,
        token_budget=token_budget,
        tokens_exceeded=tokens_exceeded,
    )


def _record_compression(stats: dict[str, Any], cfg: _config.Config) -> None:
    """Best-effort hand-off to the one telemetry owner."""
    try:
        metrics.record_compression(stats, cfg)
    except Exception:  # a metric must never change the tool result
        logger.debug("sutra: compression metric failed", exc_info=True)


def _compress_and_store(text: str, *, cfg: _config.Config, tool_name: str = "",
                        session_id: str = "") -> tuple[str, dict[str, Any]]:
    """Compress one string, retaining the original first when it is rewritten."""
    started = time.perf_counter()
    out, stats = engine.compress(text, cfg=cfg)
    # engine.compress returns the typed record; the seam mutates a plain dict
    # (stored / id / duration_ms / tool) before telemetry sees it.
    stats = dict(stats)
    stats.setdefault("stored", False)
    stats.setdefault("id", None)
    if stats.get("changed"):
        store_cfg = _store.StoreConfig.from_config(cfg)
        if cfg.store_enabled and stats["original_bytes"] <= cfg.store_max_entry_bytes:
            store = _store.Store(store_cfg, telemetry_config=cfg)
            entry = store.put(text, tool_name=tool_name, session_id=session_id)
            if entry is not None:
                # Second pass only when the original is actually on disk, so the
                # marker can quote a real id instead of promising one.
                kept_out, kept_stats = engine.compress(
                    text, ref=Ref(entry.id, cfg.store_ttl_hours), cfg=cfg)
                if kept_stats.get("changed"):
                    out, stats = kept_out, kept_stats
                    stats = dict(stats)
                    stats["stored"] = True
                    stats["id"] = entry.id
                else:
                    store.delete(entry.id)
    stats["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    stats["tool"] = str(tool_name or "")[:64]
    return out, stats


def _failed(text: str, original_bytes: int) -> Outcome:
    return Outcome(False, text, False, None, original_bytes, original_bytes)


def _record_reuse(hit: _reuse.Hit, marker: str, *, cfg: _config.Config,
                  tool_name: str, started: float) -> Outcome:
    """Serialize one exact-duplicate hit through the compression contract."""
    marker_bytes = engine.utf8_byte_length(marker) or 0
    suppressed = max(0, hit.visible_bytes - marker_bytes)
    metrics.record_reuse("hit", suppressed_bytes=suppressed,
                         original_bytes=hit.original_bytes)
    stats = {
        "changed": True,
        "rule": "reuse",
        "original_bytes": hit.original_bytes,
        "compressed_bytes": marker_bytes,
        "omitted_bytes": max(0, hit.original_bytes - marker_bytes),
        "partial": False,
        "stored": True,
        "id": hit.id,
        "fail_open": False,
        "tool": tool_name,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    _record_compression(stats, cfg)
    return Outcome(True, marker, True, hit.id, hit.original_bytes, marker_bytes)


def process_text(text: str, *, cfg: _config.Config | None = None,
                 tool_name: str = "", session_id: str = "") -> Outcome:
    """Compress one string through the existing engine and store.

    The one entry point for text-shaped hosts (CLI, MCP). Never raises: an
    internal failure returns the original text as an unchanged outcome. A
    string that cannot be encoded as UTF-8 is returned untouched with zero
    byte counts: it has no UTF-8 representation to measure or store.

    Exact repeats within one session are served from the reuse index before
    any compression work: the earlier id is verified against the store first,
    and anything that fails verification takes the normal path.
    """
    cfg = cfg or _config.resolve()
    original_bytes = engine.utf8_byte_length(text)
    if original_bytes is None:
        return Outcome(False, text, False, None, 0, 0)
    if not cfg.enabled or should_bypass(tool_name):
        return _failed(text, original_bytes)
    started = time.perf_counter()
    try:
        hit = _reuse.lookup(text, session_id=session_id, cfg=cfg)
        if hit is not None:
            marker = _reuse.marker(hit, cfg.store_ttl_hours)
            marker_bytes = engine.utf8_byte_length(marker) or 0
            if hit.visible_bytes > marker_bytes:
                return _record_reuse(hit, marker, cfg=cfg, tool_name=tool_name,
                                     started=started)
            # A marker that costs more than the repeat it replaces is not an
            # optimization; forget it so the digest is not considered again.
            metrics.record_reuse("skipped")
            _reuse.forget(session_id, text)
    except Exception:  # reuse is optional; compression must still run
        logger.debug("sutra: reuse failed; compressing normally", exc_info=True)
    try:
        out, stats = _compress_and_store(
            text, cfg=cfg, tool_name=tool_name, session_id=session_id)
    except Exception:  # fail open, and say so in the log rather than in the result
        logger.exception("sutra: compression failed; returning the original")
        return _failed(text, original_bytes)
    _record_compression(stats, cfg)
    if (cfg.reuse and stats.get("stored") and stats.get("id")
            and stats.get("rule") in _reuse.REUSABLE_RULES):
        _reuse.remember(text, session_id=session_id, entry_id=str(stats["id"]),
                        visible_bytes=int(stats.get("compressed_bytes") or 0),
                        cfg=cfg)
    compressed_bytes = stats.get("compressed_bytes")
    if compressed_bytes is None:
        compressed_bytes = engine.utf8_byte_length(out)
        if compressed_bytes is None:
            # A host adapter must never turn a string it cannot measure into an
            # exception. This is defensive; the engine already refuses one.
            compressed_bytes = original_bytes
    return Outcome(
        changed=bool(stats.get("changed")),
        output=out,
        marker="[sutra:" in out,
        id=stats.get("id"),
        original_bytes=int(stats.get("original_bytes", original_bytes)),
        compressed_bytes=int(compressed_bytes),
    )


def process_result(result: Any, *, tool_name: str | None = None,
                   session_id: str | None = None) -> Any:
    """Compress a tool result of any common shape without changing its type.

    The one entry point for result-shaped hosts (Hermes middleware, Claude Code
    hook). Never raises; non-text shapes pass through by identity.
    """
    if should_bypass(tool_name):
        return result
    cfg = _config.resolve()
    if not cfg.enabled:
        return result
    try:
        if isinstance(result, str):
            return process_text(
                result, cfg=cfg, tool_name=tool_name or "",
                session_id=session_id or "").output
        if isinstance(result, dict):
            changed = False
            new = dict(result)
            for key in TEXT_KEYS:
                value = new.get(key)
                if isinstance(value, str):
                    outcome = process_text(
                        value, cfg=cfg, tool_name=tool_name or "",
                        session_id=session_id or "")
                    if outcome.changed:
                        new[key] = outcome.output
                        changed = True
            return new if changed else result
    except Exception:  # an exotic result shape is never worth a broken tool call
        logger.exception("sutra: compression failed; returning the original result")
        return result
    return result


def restore(args: dict | None, *, store: Any = None) -> str:
    """The recovery protocol through the same seam; adapters expose it as a tool."""
    return recovery.restore(args, store=store)


def outcome_json(outcome: Outcome) -> dict[str, Any]:
    """The one serialization of the compress contract shared by CLI and MCP."""
    return {
        "changed": outcome.changed,
        "output": outcome.output,
        "marker": outcome.marker,
        "id": outcome.id,
        "original_bytes": outcome.original_bytes,
        "compressed_bytes": outcome.compressed_bytes,
    }


__all__ = [
    "CompressionStats",
    "Contract",
    "Limits",
    "NO_COMPRESS_TOOLS",
    "Outcome",
    "TEXT_KEYS",
    "budget_contract",
    "outcome_json",
    "process_result",
    "process_text",
    "restore",
    "should_bypass",
]
