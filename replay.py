"""Bounded reuse of read-only tool results before the call runs.

Every other SUTRA surface sees a tool result *after* the work happened, so it
can only shrink what was already produced. A host that routes calls through a
pre-dispatch frame — the Hermes ``tool_execution`` middleware — can instead ask
whether the same inspection still needs to run at all.

This module answers that question, and answers it conservatively. A result is
reused only when every one of these holds:

* the tool is a structured read-only inspection tool, never a command;
* the call names at least one existing local *file*, so there is something
  whose freshness can actually be stamped. Directories are refused on purpose:
  a directory's own stamp does not cover the contents of the files inside it,
  so a listing or a recursive search could otherwise replay a stale answer
  after a file changed;
* no argument is a URL, because remote content can change without a local
  stamp noticing;
* the recording session matches the current one — results never cross sessions;
* every stamped file still has the same size, mtime and content digest;
* the entry is inside its TTL and the stored result fits the size bound.

Anything unproven executes normally. A missing dependency stamp is not proof of
freshness, so an unrecognised argument shape costs one redundant call rather
than one stale answer. The store is best-effort throughout: a corrupt file, a
full disk or a serialisation failure disables reuse for that call and never
fails the tool.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import redundancy

VERSION = 1

#: Tools that only observe. Names are matched on word tokens so
#: ``read_file``, ``filesystem.read`` and ``read`` all qualify, while any
#: mutation token rejects the call outright.
READ_TOKENS = frozenset({
    "cat", "check", "describe", "diff", "find", "get", "glob", "grep",
    "head", "inspect", "list", "ls", "open", "peek", "read", "search",
    "show", "stat", "tail", "tree", "view", "walk", "wc",
})

#: Any of these makes the call ineligible no matter what else it says. A tool
#: named ``read_write`` or an argument named ``command`` is not an inspection.
MUTATION_TOKENS = frozenset({
    "apply", "append", "bash", "chmod", "chown", "commit", "copy", "cp",
    "create", "delete", "deploy", "edit", "exec", "execute", "install",
    "kill", "mkdir", "move", "mv", "patch", "post", "publish", "push",
    "put", "remove", "rename", "replace", "restart", "rm", "run", "send",
    "shell", "sh", "spawn", "start", "stop", "touch", "truncate", "upload",
    "write", "wget", "curl",
})

#: Argument names that usually carry a filesystem path.
PATH_KEYS = frozenset({
    "dir", "directory", "file", "file_path", "filename", "files", "folder",
    "path", "paths", "root", "target", "cwd", "workdir",
})

DEFAULT_TTL_SECONDS = 600
DEFAULT_MAX_ENTRIES = 64
DEFAULT_MAX_RESULT_BYTES = 256 * 1024

_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*")


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(str(value or "").lower())


def readable_name(tool_name: str | None) -> bool:
    """True when the tool name is inspection-shaped and not mutation-shaped."""
    names = _tokens(tool_name or "")
    if not names:
        return False
    if any(token in MUTATION_TOKENS for token in names):
        return False
    return any(token in READ_TOKENS for token in names)


def _string_values(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 4:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        out: list[str] = []
        for item in value.values():
            out.extend(_string_values(item, depth + 1))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        out = []
        for item in value:
            out.extend(_string_values(item, depth + 1))
        return out
    return ()


def argument_paths(args: Any) -> list[str]:
    """The paths a call names, in stable order, as the caller wrote them."""
    return _argument_paths(args)


def _argument_paths(args: Any) -> list[str]:
    """Existing local paths named by the call's arguments, in stable order."""
    if isinstance(args, str):
        candidates: list[str] = [args] if args and not args.lstrip().startswith("-") else []
    elif isinstance(args, Mapping):
        candidates = []
        for key in sorted(args, key=str):
            if str(key).lower() in PATH_KEYS:
                candidates.extend(_string_values(args[key]))
    else:
        candidates = list(_string_values(args))
    paths: list[str] = []
    for item in candidates:
        text = str(item).strip()
        if not text or "://" in text or text.startswith("-"):
            continue
        if text not in paths:
            paths.append(text)
    return paths


def _file_paths(args: Any, root: str | os.PathLike[str] | None = None) -> list[str]:
    """Named paths that are existing regular files, resolved against ``root``."""
    base = Path(root or os.getcwd())
    files: list[str] = []
    for item in _argument_paths(args):
        candidate = Path(item)
        absolute = candidate if candidate.is_absolute() else base / candidate
        try:
            if absolute.is_file():
                files.append(item)
        except OSError:
            continue
    return files


def _mentions_url(args: Any) -> bool:
    return any("://" in value for value in _string_values(args))


def canonical_arguments(args: Any) -> str:
    """Stable text for the fingerprint: dict key order must not matter."""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, sort_keys=True, separators=(",", ":"),
                          default=repr)
    except (TypeError, ValueError):
        return repr(args)


@dataclass(frozen=True)
class Hit:
    """One reusable result and the evidence it was valid at lookup time."""

    value: Any
    fingerprint: str
    paths: tuple[str, ...]
    age_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {"fingerprint": self.fingerprint, "paths": list(self.paths),
                "age_seconds": round(self.age_seconds, 3)}


@dataclass(frozen=True)
class Miss:
    """Why a call was not reused; the reason is the honest audit record."""

    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"reason": self.reason}


@dataclass
class Store:
    """A bounded, per-session store of read-only tool results."""

    path: str | os.PathLike[str]
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES
    enabled: bool = True
    _entries: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.ttl_seconds = int(self.ttl_seconds)
        self.max_entries = max(1, int(self.max_entries))
        self.max_result_bytes = max(1024, int(self.max_result_bytes))
        self.hits = 0
        self.misses = 0
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            return
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return
        self._entries = {
            str(key): dict(value) for key, value in entries.items()
            if isinstance(value, dict) and "result" in value
        }

    def save(self) -> None:
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": VERSION, "entries": self._entries}
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
            handle, temporary = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".replay.", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, self.path)
            finally:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        except (OSError, TypeError, ValueError):
            pass

    def _evict(self) -> None:
        if len(self._entries) <= self.max_entries:
            return
        ordered = sorted(self._entries.items(),
                         key=lambda item: float(item[1].get("created", 0.0)))
        for key, _ in ordered[:len(self._entries) - self.max_entries]:
            self._entries.pop(key, None)

    # -- decisions ---------------------------------------------------------

    def lookup(self, tool_name: str | None, args: Any, *,
               session_id: str | None, root: str | os.PathLike[str] | None = None,
               now: float | None = None) -> Hit | Miss:
        """Return the recorded result when reuse is provably safe."""
        if not self.enabled:
            return Miss("reuse is disabled")
        if not session_id:
            return Miss("no session id: results never cross sessions")
        if not readable_name(tool_name):
            return Miss(f"{tool_name!r} is not a read-only inspection tool")
        if _mentions_url(args):
            return Miss("the call reads remote content that no local stamp covers")
        paths = _file_paths(args, root)
        if not paths:
            return Miss("the call names no existing file whose content can be "
                        "stamped; a directory stamp does not cover its files")
        snapshot = redundancy.snapshot(paths, root=root or os.getcwd())
        if all(stamp == "missing" for stamp in snapshot.files.values()):
            return Miss("none of the named files exist")
        key = self._key(session_id, tool_name, args)
        entry = self._entries.get(key)
        if not isinstance(entry, dict):
            return Miss("no recorded result for this call in this session")
        timestamp = time.time() if now is None else float(now)
        try:
            created = float(entry.get("created", 0.0))
        except (TypeError, ValueError):
            return Miss("the entry has no usable timestamp")
        age = timestamp - created
        if self.ttl_seconds >= 0 and age > self.ttl_seconds:
            return Miss(f"the recorded result is {age:.0f}s old and has expired")
        recorded = redundancy.Snapshot(
            files=dict(entry.get("files") or {}),
            versions=dict(entry.get("versions") or {}))
        changed = recorded.changed(snapshot)
        if changed:
            return Miss("dependencies changed: " + ", ".join(changed[:5]))
        self.hits += 1
        return Hit(value=entry.get("result"), fingerprint=key,
                   paths=tuple(sorted(recorded.files)), age_seconds=age)

    def remember(self, tool_name: str | None, args: Any, result: Any, *,
                 session_id: str | None, root: str | os.PathLike[str] | None = None,
                 now: float | None = None) -> bool:
        """Record one result, or refuse when it cannot be proven replayable."""
        if not self.enabled or not session_id or not readable_name(tool_name):
            return False
        if _mentions_url(args):
            return False
        paths = _file_paths(args, root)
        if not paths:
            return False
        snapshot = redundancy.snapshot(paths, root=root or os.getcwd())
        if all(stamp == "missing" for stamp in snapshot.files.values()):
            return False
        try:
            encoded = json.dumps(result)
        except (TypeError, ValueError):
            return False
        if len(encoded.encode("utf-8")) > self.max_result_bytes:
            return False
        key = self._key(session_id, tool_name, args)
        self._entries[key] = {
            "created": time.time() if now is None else float(now),
            "tool": str(tool_name),
            "result": result,
            **snapshot.as_dict(),
        }
        self._evict()
        self.save()
        return True

    def forget(self, tool_name: str | None, args: Any, *,
               session_id: str | None) -> bool:
        key = self._key(session_id, tool_name, args)
        removed = self._entries.pop(key, None) is not None
        if removed:
            self.save()
        return removed

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        self.save()
        return count

    def __len__(self) -> int:
        return len(self._entries)

    def stats(self) -> dict[str, object]:
        return {"entries": len(self._entries), "hits": self.hits,
                "misses": self.misses, "ttl_seconds": self.ttl_seconds,
                "max_entries": self.max_entries, "path": str(self.path)}

    @staticmethod
    def _key(session_id: str | None, tool_name: str | None, args: Any) -> str:
        payload = json.dumps([str(session_id or ""), str(tool_name or ""),
                              canonical_arguments(args)], sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


def store_for(config: Any) -> Store | None:
    """Build the store the configuration describes, or ``None`` when off."""
    resolve = getattr(config, "resolve", None)
    cfg = resolve() if callable(resolve) else config
    if cfg is None or not getattr(cfg, "enabled", True):
        return None
    if str(getattr(cfg, "mode", "")) == "off":
        return None
    if not getattr(cfg, "replay_enabled", True):
        return None
    return Store(
        getattr(cfg, "replay_path", None) or os.path.join(
            os.path.expanduser("~"), ".sutra", "replay.json"),
        ttl_seconds=getattr(cfg, "replay_ttl_seconds", DEFAULT_TTL_SECONDS),
        max_entries=getattr(cfg, "replay_max_entries", DEFAULT_MAX_ENTRIES),
        max_result_bytes=getattr(cfg, "replay_max_result_bytes",
                                 DEFAULT_MAX_RESULT_BYTES))


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_RESULT_BYTES",
    "DEFAULT_TTL_SECONDS",
    "Hit",
    "MUTATION_TOKENS",
    "_file_paths",
    "Miss",
    "PATH_KEYS",
    "READ_TOKENS",
    "Store",
    "canonical_arguments",
    "readable_name",
    "store_for",
]
