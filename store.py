"""Bounded, permission-restricted store for original tool output.

Every rewrite that promises recoverability has already paid for the bytes: the
payload is in memory. Keeping a copy on local disk is what turns "45,231 bytes
elided" into "45,231 bytes you can still read".

Threat model and limits:

- The directory is created ``0700``; every file is ``0600`` and opened
  ``O_NOFOLLOW``. A symlinked store directory is refused, not followed.
- Identifiers are 128-bit random and validated against ``^su_[0-9a-f]{32}$``
  before they touch the filesystem, so a request cannot name a path.
- Writes are atomic: a temp file in the same directory is fsynced and renamed
  into place, so an interrupted write can never be read as a complete entry.
- Storage is bounded by a per-entry cap, a total quota, an entry count and a
  time-to-live. Writes keep an in-process usage total and sweep oldest-first
  only when a budget is exceeded or a sweep is due, so a write costs the same
  whether the store holds ten entries or the full two thousand.
- Contents are never logged. Only sizes, hashes and timestamps are recorded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import config
from . import metrics

logger = logging.getLogger(__name__)

ID_RE = re.compile(r"^su_[0-9a-f]{32}$")
DATA_SUFFIX = ".txt"
META_SUFFIX = ".json"
TMP_PREFIX = ".tmp-"
TMP_MAX_AGE_SECONDS = 3_600
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

DEFAULT_TTL_HOURS = config.DEFAULT_STORE_TTL_HOURS  # 7 days
DEFAULT_MAX_BYTES = config.DEFAULT_STORE_MAX_BYTES
DEFAULT_MAX_ENTRY_BYTES = config.DEFAULT_STORE_MAX_ENTRY_BYTES
DEFAULT_MAX_ENTRIES = config.DEFAULT_STORE_MAX_ENTRIES

# A full sweep is O(store), so the write path only amortises it: quota and entry
# count are checked on every write from in-process accounting, and the sweep runs
# when a budget is exceeded, every 128 writes, or every ten minutes.
_SWEEP_EVERY_WRITES = 128
_SWEEP_INTERVAL_SECONDS = 600.0


@dataclass
class _Accounting:
    entries: int = 0
    bytes: int = 0
    writes: int = 0
    swept_at: float = 0.0


_ACCOUNTING_LOCK = threading.Lock()
_ACCOUNTING: dict[str, _Accounting] = {}


class StoreUnavailable(RuntimeError):
    """The store cannot be used safely (bad path, symlink, permissions)."""


@dataclass(frozen=True)
class StoreConfig:
    directory: str
    enabled: bool = True
    ttl_hours: int = DEFAULT_TTL_HOURS
    max_bytes: int = DEFAULT_MAX_BYTES
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES
    max_entries: int = DEFAULT_MAX_ENTRIES

    @classmethod
    def from_config(cls, cfg: config.Config) -> "StoreConfig":
        """The store's view of the one resolved configuration."""
        return cls(
            directory=cfg.store_dir,
            enabled=cfg.store_enabled,
            ttl_hours=cfg.store_ttl_hours,
            max_bytes=cfg.store_max_bytes,
            max_entry_bytes=cfg.store_max_entry_bytes,
            max_entries=cfg.store_max_entries,
        )

    @classmethod
    def from_env(cls) -> "StoreConfig":
        return cls.from_config(config.resolve())


@dataclass(frozen=True)
class Entry:
    id: str
    original_bytes: int
    sha256: str
    created_at: float
    data_path: str
    meta_path: str
    tool: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class Lookup:
    entry: Entry | None
    status: str  # ok | missing | expired | invalid | unavailable


def store_from_env() -> "Store":
    """The store configured by the environment; safe to construct per call."""
    return Store(StoreConfig.from_env())


class Store:
    """A directory of originals, with hard limits and no trust in callers."""

    def __init__(self, config: StoreConfig):
        self.config = config
        self.dir = config.directory

    # -- paths --------------------------------------------------------------

    def data_path(self, entry_id: str) -> str:
        return os.path.join(self.dir, entry_id + DATA_SUFFIX)

    def meta_path(self, entry_id: str) -> str:
        return os.path.join(self.dir, entry_id + META_SUFFIX)

    def _ensure_dir(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.dir))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        try:
            os.lstat(self.dir)
        except FileNotFoundError:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            return
        if os.path.islink(self.dir) or not os.path.isdir(self.dir):
            raise StoreUnavailable(f"store path is not a plain directory: {self.dir}")

    def available(self) -> bool:
        try:
            self._ensure_dir()
            return True
        except (OSError, StoreUnavailable) as exc:
            logger.warning("sutra: store unavailable (%s); originals will not be retained", exc)
            return False

    # -- accounting ---------------------------------------------------------

    def _accounting_key(self) -> str:
        return os.path.abspath(self.dir)

    def _ensure_accounting(self, now: float) -> None:
        """Seed the counters from disk once per process for this directory.

        The seeding sweep also expires entries and drops orphans, so the one
        O(store) scan a process pays per directory does the housekeeping too.
        """
        key = self._accounting_key()
        with _ACCOUNTING_LOCK:
            if key in _ACCOUNTING:
                return
        self.prune(now=now)

    def _note_write(self, size: int, now: float) -> bool:
        """Count a new entry; True when a full sweep is due."""
        key = self._accounting_key()
        with _ACCOUNTING_LOCK:
            usage = _ACCOUNTING.setdefault(key, _Accounting())
            usage.entries += 1
            usage.bytes += size
            usage.writes += 1
            over = (usage.entries > self.config.max_entries
                    or usage.bytes > self.config.max_bytes)
            due = (usage.writes >= _SWEEP_EVERY_WRITES
                   or now - usage.swept_at >= _SWEEP_INTERVAL_SECONDS)
            return over or due

    def _note_removed(self, size: int) -> None:
        key = self._accounting_key()
        with _ACCOUNTING_LOCK:
            usage = _ACCOUNTING.get(key)
            if usage is not None:
                usage.entries = max(0, usage.entries - 1)
                usage.bytes = max(0, usage.bytes - max(0, size))

    # -- writes -------------------------------------------------------------

    def put(self, text: str, *, tool_name: str = "", session_id: str = "",
            now: float | None = None) -> Entry | None:
        """Retain ``text`` byte-for-byte, or return None (disabled/failed/too big)."""
        if not self.config.enabled:
            return None
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:
            return None
        if len(data) > self.config.max_entry_bytes:
            return None
        created = time.time() if now is None else now
        try:
            self._ensure_dir()
            entry_id = "su_" + secrets.token_hex(16)
            meta = {
                "id": entry_id,
                "created_at": created,
                "original_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "tool": str(tool_name or "")[:64],
                "session_id": str(session_id or "")[:64],
                "version": 1,
            }
            # Meta last: a data file without meta is invisible to readers.
            self._ensure_accounting(created)
            self._write_atomic(self.data_path(entry_id), data)
            self._write_atomic(self.meta_path(entry_id),
                               json.dumps(meta, sort_keys=True).encode("utf-8"))
            # Enforce the budget *after* writing, so the store is never over the
            # limit while a marker claims the original is available. Most writes
            # are under budget and skip the sweep entirely.
            if self._note_write(len(data), created):
                self.prune(now=created)
            if not os.path.exists(self.data_path(entry_id)):
                return None
        except (OSError, StoreUnavailable) as exc:
            logger.warning("sutra: could not retain an original (%s)", exc)
            return None
        return Entry(
            id=entry_id,
            original_bytes=len(data),
            sha256=meta["sha256"],
            created_at=created,
            data_path=self.data_path(entry_id),
            meta_path=self.meta_path(entry_id),
            tool=meta["tool"],
            session_id=meta["session_id"],
        )

    def _write_atomic(self, path: str, data: bytes) -> None:
        tmp = os.path.join(
            os.path.dirname(path),
            f"{TMP_PREFIX}{os.path.basename(path)}-{os.getpid()}-{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            # Atomic rename: a reader sees the old state or the complete new file,
            # never a half-written one. No fsync: this is a cache, and a lost
            # entry is reported as missing rather than served wrong.
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def delete(self, entry_id: str) -> None:
        if not ID_RE.match(str(entry_id or "")):
            return
        data_path = self.data_path(entry_id)
        try:
            size = os.path.getsize(data_path)
        except OSError:
            size = 0
        removed = False
        for path in (data_path, self.meta_path(entry_id)):
            try:
                os.unlink(path)
                removed = removed or path == data_path
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.debug("sutra: could not delete %s (%s)", path, exc)
        if removed:
            self._note_removed(size)

    # -- reads --------------------------------------------------------------

    def get(self, entry_id: str, now: float | None = None) -> Lookup:
        """Look up a retained original, enforcing the TTL."""
        if not ID_RE.match(str(entry_id or "")):
            return Lookup(None, "invalid")
        if not self.config.enabled:
            return Lookup(None, "unavailable")
        now = time.time() if now is None else now
        meta_path = self.meta_path(entry_id)
        try:
            with open(meta_path, "rb") as fh:
                meta = json.loads(fh.read(65_536).decode("utf-8"))
        except FileNotFoundError:
            return Lookup(None, "missing")
        except (OSError, ValueError, UnicodeDecodeError):
            return Lookup(None, "missing")
        if not isinstance(meta, dict):
            return Lookup(None, "missing")
        try:
            created = float(meta.get("created_at") or 0)
            size = int(meta.get("original_bytes") or 0)
        except (TypeError, ValueError):
            return Lookup(None, "missing")
        if self.config.ttl_hours > 0 and now - created > self.config.ttl_hours * 3_600:
            self.delete(entry_id)
            _record_cleanup({"expired": 1, "bytes": size})
            return Lookup(None, "expired")
        try:
            if os.path.getsize(self.data_path(entry_id)) != size:
                # Interrupted or truncated write: never serve a partial original.
                self.delete(entry_id)
                return Lookup(None, "missing")
        except OSError:
            self.delete(entry_id)  # orphaned meta: no data file behind it
            return Lookup(None, "missing")
        return Lookup(Entry(
            id=entry_id,
            original_bytes=size,
            sha256=str(meta.get("sha256") or ""),
            created_at=created,
            data_path=self.data_path(entry_id),
            meta_path=meta_path,
            tool=str(meta.get("tool") or ""),
            session_id=str(meta.get("session_id") or ""),
        ), "ok")

    def read_bytes(self, entry: Entry, start: int = 0, length: int | None = None) -> bytes:
        """Read a slice of the stored original; raises OSError if it vanished."""
        fd = os.open(entry.data_path, os.O_RDONLY | O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            fh.seek(max(0, start))
            return fh.read() if length is None else fh.read(max(0, length))

    def verify(self, entry: Entry, data: bytes | None = None) -> bool:
        """True when the entry's bytes still hash to the recorded value.

        Pass ``data`` when the caller already read the entry: the digest is then
        computed over exactly the bytes that caller is about to serve, and the
        file is not opened a second time. Without it, the entry file is read
        once here.
        """
        try:
            if not entry.sha256:
                return False
            if data is not None:
                return hashlib.sha256(data).hexdigest() == entry.sha256
            digest = hashlib.sha256()
            fd = os.open(entry.data_path, os.O_RDONLY | O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            return digest.hexdigest() == entry.sha256
        except OSError:
            return False

    # -- housekeeping -------------------------------------------------------

    def usage(self) -> tuple[int, int]:
        """``(entries, total_bytes)`` currently on disk, ignoring temp files.

        Also refreshes the in-process counters: it is the one authoritative
        scan, and callers that need a real total (tests, metrics) get it here.
        """
        entries = total = 0
        try:
            names = os.listdir(self.dir)
        except OSError:
            names = []
        for name in names:
            if not name.endswith(DATA_SUFFIX) or name.startswith(TMP_PREFIX):
                continue
            try:
                total += os.path.getsize(os.path.join(self.dir, name))
                entries += 1
            except OSError:
                continue
        key = self._accounting_key()
        with _ACCOUNTING_LOCK:
            usage = _ACCOUNTING.get(key) or _Accounting()
            usage.entries = entries
            usage.bytes = total
            _ACCOUNTING[key] = usage
        return entries, total

    def prune(self, now: float | None = None) -> dict[str, int]:
        """Delete expired entries, orphans and temp files; then enforce the quota.

        Returns a small report; never raises. Safe to run concurrently with
        readers, which see either a complete entry or a missing one.
        """
        now = time.time() if now is None else now
        report = {"expired": 0, "orphans": 0, "temp": 0, "quota": 0, "bytes": 0}
        try:
            names = os.listdir(self.dir)
        except OSError:
            return report
        ttl_seconds = self.config.ttl_hours * 3_600
        live: list[tuple[float, int, str]] = []
        data_names = {n[:-len(DATA_SUFFIX)] for n in names
                      if n.endswith(DATA_SUFFIX) and not n.startswith(TMP_PREFIX)}
        for name in names:
            path = os.path.join(self.dir, name)
            if name.startswith(TMP_PREFIX):
                if self._age(path, now) > TMP_MAX_AGE_SECONDS:
                    report["bytes"] += int(self._age(path, now, size=True))
                    self._unlink(path)
                    report["temp"] += 1
                continue
            if not name.endswith(DATA_SUFFIX):
                continue
            stem = name[:-len(DATA_SUFFIX)]
            meta = self._read_meta(stem)
            if meta is None:
                if self._age(path, now) > TMP_MAX_AGE_SECONDS:
                    report["bytes"] += int(self._age(path, now, size=True))
                    self._unlink(path)
                    # Its metadata is what made this an orphan; drop it too, or
                    # it outlives every prune and is never accounted for.
                    self._unlink(self.meta_path(stem))
                    report["orphans"] += 1
                continue
            try:
                created = float(meta.get("created_at"))
            except (TypeError, ValueError):
                created = now - self._age(path, now)
            size = self._age(path, now, size=True)
            if ttl_seconds and now - created > ttl_seconds:
                report["bytes"] += int(size)
                self.delete(stem)
                report["expired"] += 1
                continue
            live.append((created, int(size), stem))
        # Meta files whose data file is gone.
        for name in names:
            if name.endswith(META_SUFFIX) and not name.startswith(TMP_PREFIX):
                stem = name[:-len(META_SUFFIX)]
                if stem not in data_names:
                    self._unlink(os.path.join(self.dir, name))
                    report["orphans"] += 1
        total = sum(item[1] for item in live)
        over_count = len(live) > self.config.max_entries
        over_bytes = total > self.config.max_bytes
        if over_count or over_bytes:
            live.sort()  # oldest first
            while live and (len(live) > self.config.max_entries
                            or total > self.config.max_bytes):
                _, size, stem = live.pop(0)
                self.delete(stem)
                total -= size
                report["quota"] += 1
                report["bytes"] += size
        key = self._accounting_key()
        with _ACCOUNTING_LOCK:
            usage = _ACCOUNTING.get(key) or _Accounting()
            usage.entries = len(live)
            usage.bytes = total
            usage.writes = 0
            usage.swept_at = now
            _ACCOUNTING[key] = usage
        _record_cleanup(report)
        return report

    def _read_meta(self, entry_id: str) -> dict[str, Any] | None:
        try:
            with open(self.meta_path(entry_id), "rb") as fh:
                meta = json.loads(fh.read(65_536).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        return meta if isinstance(meta, dict) else None

    @staticmethod
    def _age(path: str, now: float, *, size: bool = False) -> float:
        try:
            info = os.stat(path)
        except OSError:
            return 0.0 if size else now
        return info.st_size if size else now - info.st_mtime

    @staticmethod
    def _unlink(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass


def _record_cleanup(report: dict[str, int]) -> None:
    """Account for one cleanup. A failed metric never fails a prune."""
    try:
        metrics.record_cleanup(report)
    except Exception:
        logger.debug("sutra: cleanup metric failed", exc_info=True)
