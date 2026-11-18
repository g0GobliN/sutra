"""Dependency-aware classification of repeated tool work (goals.md PART 7).

Exact-output reuse (``reuse.py``) answers "is this the same bytes I already
saw?". This module answers the harder question a host asks before it runs a
call again: may this call be skipped, must it be revalidated, or does it have
to execute no matter what?

Four answers, deliberately conservative:

- ``SAFE_TO_REUSE`` — the same call was recorded with the same dependency
  snapshot (file hashes, sizes and mtimes, plus optional version stamps) and
  the record has not expired.
- ``REQUIRES_REVALIDATION`` — a record exists but a dependency changed or the
  record expired. A host may re-check cheaply instead of rerunning blindly.
- ``MUST_EXECUTE`` — the call looks like a test, lint, type-check, security or
  verification command. Those are never skipped, matching the goals document's
  rule that mandatory verification is not an optimization target.
- ``UNKNOWN`` — no record, or no dependency information to compare.

The module stores a bounded JSON ledger. It never runs a command, never stores
a tool result, and never decides on its own that work may be skipped: it
returns a classification with a reason, and the caller stays responsible.

A tool-result hook cannot observe tool *calls*, so this ledger is usable from
the CLI and from a host that chooses to ask. That limitation is stated in
``docs/redundancy.md`` instead of being papered over.
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
from typing import Iterable, Mapping

VERSION = 1
DEFAULT_TTL_SECONDS = 3600
DEFAULT_MAX_ENTRIES = 512
MAX_FILE_HASH_BYTES = 1 << 20  # 1 MiB; larger files are compared by metadata
_MANDATORY_TOKENS = frozenset({
    "test", "tests", "pytest", "unittest", "tox", "nox", "jest", "vitest",
    "mocha", "rspec", "phpunit", "ctest", "audit", "auditing", "security",
    "sast", "dast", "scan", "scanner", "lint", "linting", "typecheck",
    "type-check", "mypy", "tsc", "ruff", "flake8", "pylint", "eslint",
    "bandit", "semgrep", "trivy", "grype", "pip-audit", "verify", "check",
})
_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*")


class Classification(str):
    """One of the four answers; plain ``str`` so JSON stays readable."""


SAFE_TO_REUSE = "SAFE_TO_REUSE"
REQUIRES_REVALIDATION = "REQUIRES_REVALIDATION"
MUST_EXECUTE = "MUST_EXECUTE"
UNKNOWN = "UNKNOWN"


def mandatory_command(command: str) -> bool:
    """True when a command is test/lint/security/verification shaped."""
    if not isinstance(command, str):
        return False
    for token in _TOKEN.findall(command.lower()):
        if token in _MANDATORY_TOKENS:
            return True
        # ./verify-all and type-check still carry the mandatory word.
        if any(part in _MANDATORY_TOKENS for part in token.split("-")):
            return True
    return False


def _file_stamp(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    if stat.st_size > MAX_FILE_HASH_BYTES:
        return f"{stat.st_size}:{stat.st_mtime_ns}:unhashed"
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return f"{stat.st_size}:{stat.st_mtime_ns}:unreadable"
    return f"{stat.st_size}:{stat.st_mtime_ns}:{digest}"


@dataclass(frozen=True)
class Snapshot:
    """What a result depended on, as comparable strings."""

    files: Mapping[str, str] = field(default_factory=dict)
    versions: Mapping[str, str] = field(default_factory=dict)

    def digest(self) -> str:
        payload = json.dumps(
            {"files": dict(sorted(self.files.items())),
             "versions": dict(sorted(self.versions.items()))},
            sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def changed(self, other: "Snapshot") -> list[str]:
        """Names that differ between two snapshots, in stable order."""
        names = set(self.files) | set(other.files) | set(self.versions) | set(other.versions)
        return sorted(name for name in names
                      if self.files.get(name) != other.files.get(name)
                      or self.versions.get(name) != other.versions.get(name))

    def as_dict(self) -> dict[str, object]:
        return {"files": dict(sorted(self.files.items())),
                "versions": dict(sorted(self.versions.items()))}


def snapshot(paths: Iterable[str | os.PathLike[str]] = (), *,
             root: str | os.PathLike[str] = ".",
             versions: Mapping[str, str] | None = None) -> Snapshot:
    """Capture dependency stamps for ``paths``, resolved against ``root``."""
    base = Path(root)
    files: dict[str, str] = {}
    for item in paths:
        path = Path(item)
        absolute = path if path.is_absolute() else base / path
        try:
            key = str(absolute.relative_to(base.resolve()))
        except ValueError:
            key = str(absolute)
        files[key] = _file_stamp(absolute)
    return Snapshot(files=files, versions=dict(versions or {}))


@dataclass(frozen=True)
class Decision:
    """The classification and the reason a caller can print or log."""

    classification: str
    reason: str
    changed: tuple[str, ...] = ()

    @property
    def reusable(self) -> bool:
        return self.classification == SAFE_TO_REUSE

    def as_dict(self) -> dict[str, object]:
        return {"classification": self.classification, "reason": self.reason,
                "changed": list(self.changed), "reusable": self.reusable}


def call_fingerprint(tool: str, arguments: str = "") -> str:
    """Stable key for one call: tool name plus its canonical arguments."""
    payload = json.dumps([str(tool), str(arguments)], sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


class Ledger:
    """A bounded, atomically written JSON record of calls and dependencies."""

    def __init__(self, path: str | os.PathLike[str],
                 *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.path = Path(path)
        # A negative TTL means "never expires"; the ledger records it as is.
        self.ttl_seconds = (DEFAULT_TTL_SECONDS if ttl_seconds is None
                            else int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._entries: dict[str, dict[str, object]] = {}
        self._load()

    @classmethod
    def from_env(cls) -> "Ledger":
        raw = os.environ.get("SUTRA_REDUNDANCY_FILE", "").strip()
        path = Path(raw) if raw else Path.home() / ".sutra" / "redundancy.json"
        return cls(path)

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._entries = {}
            return
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            self._entries = {}
            return
        entries = payload.get("entries")
        if isinstance(entries, dict):
            self._entries = {
                str(key): value for key, value in entries.items()
                if isinstance(value, dict)
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "entries": self._entries}
        data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
        handle, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".redundancy.", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def _evict(self) -> None:
        if len(self._entries) <= self.max_entries:
            return
        ordered = sorted(
            self._entries.items(),
            key=lambda item: float(item[1].get("created", 0.0)))
        for key, _ in ordered[:len(self._entries) - self.max_entries]:
            self._entries.pop(key, None)

    # -- decisions ---------------------------------------------------------

    def classify(self, key: str, *, dependencies: Snapshot | None = None,
                 command: str = "", now: float | None = None) -> Decision:
        """Classify a call without changing the ledger."""
        if command and mandatory_command(command):
            return Decision(
                MUST_EXECUTE,
                f"{command.strip()!r} looks like a verification command; "
                "tests, lint, type checks and security scans are never skipped")
        entry = self._entries.get(key)
        if not isinstance(entry, dict):
            return Decision(UNKNOWN, "no recorded result for this call")
        timestamp = now if now is not None else time.time()
        try:
            created = float(entry.get("created", 0.0))
        except (TypeError, ValueError):
            return Decision(UNKNOWN, "recorded result has no usable timestamp")
        ttl = entry.get("ttl", self.ttl_seconds)
        try:
            ttl_value = float(ttl)
        except (TypeError, ValueError):
            ttl_value = float(self.ttl_seconds)
        if ttl_value >= 0 and timestamp - created > ttl_value:
            return Decision(REQUIRES_REVALIDATION,
                            "the recorded result expired; revalidate it")
        recorded = _snapshot_from(entry)
        current = dependencies if dependencies is not None else Snapshot()
        changed = recorded.changed(current)
        if not changed:
            return Decision(SAFE_TO_REUSE,
                            "the call and all recorded dependencies are unchanged")
        return Decision(REQUIRES_REVALIDATION,
                        "dependencies changed: " + ", ".join(changed[:5])
                        + (" …" if len(changed) > 5 else ""),
                        tuple(changed))

    def record(self, key: str, *, dependencies: Snapshot | None = None,
               result_fingerprint: str = "", ttl_seconds: int | None = None,
               now: float | None = None) -> None:
        """Remember one call, its dependency snapshot and its result digest."""
        snapshot_value = dependencies if dependencies is not None else Snapshot()
        self._entries[key] = {
            "created": now if now is not None else time.time(),
            "ttl": self.ttl_seconds if ttl_seconds is None else int(ttl_seconds),
            "result": str(result_fingerprint),
            **snapshot_value.as_dict(),
        }
        entry = self._entries[key]
        entry["digest"] = snapshot_value.digest()
        self._evict()
        self.save()

    def forget(self, key: str) -> bool:
        removed = self._entries.pop(key, None) is not None
        if removed:
            self.save()
        return removed

    def entry(self, key: str) -> dict[str, object] | None:
        value = self._entries.get(key)
        return dict(value) if isinstance(value, dict) else None

    def __len__(self) -> int:
        return len(self._entries)


def _snapshot_from(entry: Mapping[str, object]) -> Snapshot:
    files = entry.get("files")
    versions = entry.get("versions")
    return Snapshot(
        files={str(key): str(value) for key, value in files.items()}
        if isinstance(files, dict) else {},
        versions={str(key): str(value) for key, value in versions.items()}
        if isinstance(versions, dict) else {},
    )


__all__ = [
    "Classification", "Decision", "Ledger", "Snapshot", "call_fingerprint",
    "mandatory_command", "snapshot", "SAFE_TO_REUSE", "REQUIRES_REVALIDATION",
    "MUST_EXECUTE", "UNKNOWN",
]
