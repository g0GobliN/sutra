"""The tool calls a session already made, so the next repeat can be avoided.

Compression saves bytes inside a result. This module saves the call itself: a
host that can stop a tool call *before* it runs (DeepSeek Harness
``tools/pre-execute``, OpenClaw ``before_tool_call``) asks whether this exact
call already produced a result that is still in the store. When it did, the
call can be denied with the recovery id, and the agent exchanges the id for the
exact bytes with ``sutra_restore``. The host runs nothing, the store serves the
same evidence, and no new bytes enter the conversation.

The key is the tool name plus the canonical JSON of its arguments, scoped to a
session: the same file read in a different session is a different decision, and
a changed argument is a different call. Whether the earlier result is still
usable is decided by the store, not by this file: an expired, pruned or
corrupted entry is never suggested.

Everything here is bounded and fail-open, in the same order of priorities as the
compression engine:

1. **Exact identity only.** The key is a SHA-256 over the session, the tool name
   and a canonical JSON encoding of the arguments. Anything that cannot be
   encoded that way produces no key, so no call is ever treated as a repeat on
   a guess.
2. **A suggestion, never an execution.** This module never returns bytes. It
   returns a recovery id, and the host decides what to do with it. Only a tool
   whose effect is its output is ever recorded: a write, a command or a fetch
   leaves the world changed, so the same bytes are not the same outcome.
2b. **The dependency stamp decides, not the name.** A read is only suggested
   while the files its arguments name still carry the size, mtime and hash they
   had when the result was stored. A record whose stamp cannot be reproduced —
   an older entry, or a relative path under a different working directory — is
   never suggested, because the index cannot prove the read would be the same
   one.
3. **Bounded memory.** At most ``MAX_SESSIONS`` sessions and
   ``MAX_ENTRIES_PER_SESSION`` keys each, oldest first. The file is small and
   rewritten whole.
4. **Fail open.** Any failure here means "run the call", which is what would
   have happened without this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time

from . import config as _config
from . import redundancy as _redundancy
from . import replay as _replay
from . import store as _store

logger = logging.getLogger(__name__)

FILE_NAME = "calls.json"
MAX_SESSIONS = 64
MAX_ENTRIES_PER_SESSION = 128
# One lock per process is enough: the file is a hint, and a lost update only
# means the next repeat runs instead of being avoided.
_LOCK = threading.Lock()


def key(session_id: str, tool_name: str, arguments: object) -> str | None:
    """The identity of one call, or None when it cannot be identified exactly."""
    if not isinstance(tool_name, str) or not tool_name:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    digest = hashlib.sha256()
    digest.update(session_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(tool_name.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(encoded.encode("utf-8"))
    return digest.hexdigest()


def path(cfg: _config.Config | None = None) -> str:
    """Where the call index lives: beside the store, never inside it."""
    cfg = cfg or _config.resolve()
    return os.path.join(cfg.store_dir, FILE_NAME)


def reusable(tool_name: str) -> bool:
    """True when skipping this tool call can only skip a read.

    Denying a call replaces its effect with the bytes it would have returned.
    That is only sound for a tool whose effect *is* its output: a write, a
    command or a network call leaves the world changed, so the same bytes are
    not the same outcome. The predicate is the one the replay index already
    uses, so both features agree on what counts as an inspection and a change
    to either list moves both.
    """
    return _replay.readable_name(tool_name)


def _read(path: str) -> dict[str, dict[str, list]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.debug("sutra: could not read the call index; every repeat will run",
                     exc_info=True)
        return {}
    if not isinstance(document, dict):
        return {}
    sessions: dict[str, dict[str, list]] = {}
    for session, entries in document.items():
        if not isinstance(session, str) or not isinstance(entries, dict):
            continue
        kept: dict[str, list] = {}
        for entry_key, value in entries.items():
            if not isinstance(entry_key, str) or not isinstance(value, list):
                continue
            if len(value) != 4 or not isinstance(value[0], str):
                continue
            kept[entry_key] = value
        sessions[session] = kept
    return sessions


def _lock_down(handle: int, temporary: str) -> None:
    """Restrict the file to its owner, with the tool the platform provides."""
    if hasattr(os, "fchmod"):
        os.fchmod(handle, 0o600)
    else:  # Windows has no fchmod; chmod after creation is the same promise
        os.chmod(temporary, 0o600)


def _write(path: str, sessions: dict[str, dict[str, list]]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    payload = json.dumps(sessions, sort_keys=True, separators=(",", ":"))
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".calls-", suffix=".tmp")
    try:
        _lock_down(handle, temporary)
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            logger.debug("sutra: a temporary call index was left behind",
                         exc_info=True)
        raise


def _prune(sessions: dict[str, dict[str, list]]) -> None:
    """Drop the oldest sessions and the oldest keys in each, in place."""
    while len(sessions) > MAX_SESSIONS:
        oldest = min(sessions, key=lambda name: min(
            (value[1] for value in sessions[name].values()), default=0.0))
        sessions.pop(oldest, None)
    for entries in sessions.values():
        while len(entries) > MAX_ENTRIES_PER_SESSION:
            oldest = min(entries, key=lambda name: entries[name][1])
            entries.pop(oldest, None)


def remember(session_id: str, tool_name: str, arguments: object, entry_id: str,
             *, cfg: _config.Config | None = None, now: float | None = None) -> bool:
    """Record that this call produced a stored result; False means "not recorded"."""
    if not entry_id:
        return False
    entry_key = key(session_id, tool_name, arguments)
    if entry_key is None or not reusable(tool_name):
        return False
    cfg = cfg or _config.resolve()
    if not cfg.enabled or not cfg.reuse_active:
        return False
    target = path(cfg)
    stamp = time.time() if now is None else now
    cwd = os.getcwd()
    recorded = _dependencies(arguments, cwd)
    try:
        with _LOCK:
            sessions = _read(target)
            entries = sessions.setdefault(session_id, {})
            entries.pop(entry_key, None)
            entries[entry_key] = [entry_id, stamp, recorded, cwd]
            _prune(sessions)
            _write(target, sessions)
        return True
    except (OSError, ValueError):
        logger.debug("sutra: could not remember a call; the next repeat will run",
                     exc_info=True)
        return False


def lookup(session_id: str, tool_name: str, arguments: object,
           *, cfg: _config.Config | None = None, now: float | None = None) -> str | None:
    """The recovery id of a result this call already produced, when still usable."""
    entry_key = key(session_id, tool_name, arguments)
    if entry_key is None or not reusable(tool_name):
        return None
    cfg = cfg or _config.resolve()
    if not cfg.enabled or not cfg.reuse_active:
        return None
    target = path(cfg)
    try:
        sessions = _read(target)
    except (OSError, ValueError):
        return None
    entries = sessions.get(session_id)
    if not entries:
        return None
    record = entries.get(entry_key)
    if not record:
        return None
    entry_id, recorded, recorded_cwd = record[0], record[2], record[3]
    if not _still_dependencies(arguments, recorded, recorded_cwd):
        # The read would no longer see what it saw; stop offering it so the
        # next call does the work instead of restoring an outdated result.
        _forget_stale(target, session_id, entry_key, entry_id)
        return None
    try:
        found = _store.store_from_env().get(entry_id, now=now)
    except Exception:  # a store we cannot reach means "run the call"
        logger.debug("sutra: could not verify a remembered call", exc_info=True)
        return None
    if found.status == "ok" and found.entry is not None:
        return entry_id
    _forget_stale(target, session_id, entry_key, entry_id)
    return None


def _dependencies(arguments: object, cwd: str) -> str:
    """The stamp of the files this call names, as the caller wrote them."""
    paths = _replay.argument_paths(arguments)
    if not paths:
        return ""
    return _redundancy.snapshot(paths, root=cwd).digest()


def _still_dependencies(arguments: object, recorded: str, recorded_cwd: str) -> bool:
    """True when the read would see the same files it saw when it was recorded.

    A call that names no path has no dependency to compare, and a stamp that
    cannot be reproduced — because the process is now in another directory and
    the call named relative paths — is treated as changed, so the call runs.
    """
    paths = _replay.argument_paths(arguments)
    if not paths:
        return recorded == ""
    cwd = os.getcwd()
    if cwd != recorded_cwd and any(not os.path.isabs(item) for item in paths):
        return False
    return _dependencies(arguments, cwd) == recorded


def _forget_stale(target: str, session_id: str, entry_key: str, entry_id: str) -> None:
    """Drop one key, but only while it still points at the entry we rejected.

    The delete re-reads under the lock and checks the id first, so a call
    re-recorded for this key between the verification and this cleanup keeps
    its new entry instead of losing it to a stale decision.
    """
    try:
        with _LOCK:
            sessions = _read(target)
            entries = sessions.get(session_id)
            record = entries.get(entry_key) if entries else None
            if not record or record[0] != entry_id:
                return
            entries.pop(entry_key, None)
            if not entries:
                sessions.pop(session_id, None)
            _write(target, sessions)
    except (OSError, ValueError):
        logger.debug("sutra: could not forget a stale call", exc_info=True)


def forget(session_id: str, tool_name: str, arguments: object,
           *, cfg: _config.Config | None = None) -> None:
    """Stop suggesting this call, whether or not it is on disk."""
    entry_key = key(session_id, tool_name, arguments)
    if entry_key is None:
        return
    cfg = cfg or _config.resolve()
    target = path(cfg)
    try:
        with _LOCK:
            sessions = _read(target)
            entries = sessions.get(session_id)
            if not entries or entry_key not in entries:
                return
            entries.pop(entry_key, None)
            if not entries:
                sessions.pop(session_id, None)
            _write(target, sessions)
    except (OSError, ValueError):
        logger.debug("sutra: could not forget a call", exc_info=True)
