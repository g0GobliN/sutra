"""Exact-duplicate reuse inside one agent session.

A compressed result is still re-sent on every following turn if the agent asks
for the same thing twice: the same file read, the same directory listing, the
same log. The bytes are identical, so the second copy carries no new evidence.
This module remembers the retained original of a result SUTRA already rewrote
and replaces an exact repeat with a small marker that quotes the earlier id.

Safety rules, in the same priority order as the compression engine:

1. **Exact identity only.** The key is the SHA-256 of the original UTF-8 text.
   A changed file, a changed log or a changed query produces a different digest
   and is never treated as a repeat.
2. **Plain text only.** Diffs, JSON documents and tracebacks keep their shape;
   they are never replaced by a marker. Only rules that produce plain prose or
   log text are remembered.
3. **The id must still be real.** Every hit is verified against the store
   (existence, size, hash and TTL) before a marker is emitted. A pruned or
   corrupted entry falls back to normal compression.
4. **Bounded memory per process.** At most 64 sessions and 128 digests per
   session, oldest-first. Nothing here is durable; a restart only means the
   next repeat is compressed normally.
5. **Fail open.** Any failure returns no hit, and the caller compresses the
   payload exactly as it would have without this module.

The marker is deliberately a normal ``[sutra: ...]`` marker: the same
``sutra_restore`` protocol and the same retention bounds apply.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass

from . import config
from . import store as _store

logger = logging.getLogger(__name__)

MAX_SESSIONS = 64
MAX_ENTRIES_PER_SESSION = 128
# Rules whose visible text is plain prose or log output. A repeated diff, JSON
# document or traceback is never replaced: its shape carries meaning.
REUSABLE_RULES = frozenset({"head-tail", "head-tail+dedupe", "dedupe", "ansi"})

_LOCK = threading.Lock()
# session key -> digest -> (entry id, visible bytes of the earlier rewrite)
_SEEN: "OrderedDict[str, OrderedDict[str, tuple[str, int]]]" = OrderedDict()


@dataclass(frozen=True)
class Hit:
    """One verified earlier rewrite of the same original bytes."""

    id: str
    original_bytes: int
    visible_bytes: int


def _session_key(session_id: str) -> str:
    return str(session_id or "")[:64]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reset() -> None:
    """Forget every session. Tests and explicit diagnostics use this."""
    with _LOCK:
        _SEEN.clear()


def tracked(session_id: str) -> int:
    """How many digests this process remembers for one session."""
    with _LOCK:
        entries = _SEEN.get(_session_key(session_id))
        return len(entries) if entries else 0


def remember(text: str, *, session_id: str, entry_id: str, visible_bytes: int,
             cfg: config.Config) -> None:
    """Remember that ``text`` was rewritten to ``visible_bytes`` under ``entry_id``."""
    try:
        if not cfg.reuse or not cfg.enabled or not session_id or not entry_id:
            return
        if len(text) < cfg.reuse_min_bytes:
            return
        digest = _digest(text)
        key = _session_key(session_id)
        with _LOCK:
            entries = _SEEN.get(key)
            if entries is None:
                entries = OrderedDict()
                _SEEN[key] = entries
                while len(_SEEN) > MAX_SESSIONS:
                    _SEEN.popitem(last=False)
            else:
                _SEEN.move_to_end(key)
            entries[digest] = (str(entry_id), max(0, int(visible_bytes)))
            entries.move_to_end(digest)
            while len(entries) > MAX_ENTRIES_PER_SESSION:
                entries.popitem(last=False)
    except Exception:  # remembering is an optimization, never a requirement
        logger.debug("sutra: could not remember a reusable result", exc_info=True)


def forget(session_id: str, text: str) -> None:
    """Drop one digest, e.g. because its entry is no longer usable."""
    try:
        key = _session_key(session_id)
        digest = _digest(text)
        with _LOCK:
            entries = _SEEN.get(key)
            if entries is not None:
                entries.pop(digest, None)
    except Exception:
        logger.debug("sutra: could not forget a reusable result", exc_info=True)


def lookup(text: str, *, session_id: str, cfg: config.Config,
           store: _store.Store | None = None) -> Hit | None:
    """Return a verified earlier rewrite of exactly ``text``, or None.

    The store lookup is the safety boundary: an id is only quoted when the
    original is still present, the recorded size is intact and the hash still
    matches. Anything else is a miss and normal compression continues.
    """
    try:
        if not cfg.reuse or not cfg.enabled or not cfg.store_enabled:
            return None
        if not session_id or len(text) < cfg.reuse_min_bytes:
            return None
        key = _session_key(session_id)
        digest = _digest(text)
        with _LOCK:
            entries = _SEEN.get(key)
            if not entries:
                return None
            found = entries.get(digest)
            if found is None:
                return None
            entries.move_to_end(digest)
        entry_id, visible_bytes = found
        store = store or _store.Store(_store.StoreConfig.from_config(cfg))
        result = store.get(entry_id)
        entry = result.entry
        if result.status != "ok" or entry is None or entry.sha256 != digest:
            forget(session_id, text)
            return None
        return Hit(entry_id, int(entry.original_bytes), max(0, int(visible_bytes)))
    except Exception:
        logger.debug("sutra: reuse lookup failed", exc_info=True)
        return None


def marker(hit: Hit, ttl_hours: int) -> str:
    """A marker the agent can act on without re-running the tool."""
    retained = f"retained {ttl_hours}h" if ttl_hours else "retained"
    return (
        f'[sutra: repeated output · {hit.original_bytes:,} B identical to an earlier '
        f'result in this session · id={hit.id} · original {retained} · retrieve: '
        f'sutra_restore(id="{hit.id}", mode="bytes", start=0, end=8192) '
        f'(page with start/end)]')


__all__ = [
    "Hit",
    "MAX_ENTRIES_PER_SESSION",
    "MAX_SESSIONS",
    "REUSABLE_RULES",
    "forget",
    "lookup",
    "marker",
    "remember",
    "reset",
    "tracked",
]
