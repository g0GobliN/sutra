"""Scoped failure tracking for optimization strategies.

The optimizer is fail-open by construction: it only ever commits a candidate the
protected-span check accepted, so a rejected transformation cannot mutate the
text it was tried on. What the loop lacked was memory: a strategy that raises on
one shape of payload would be retried on every later payload of that shape.

This module adds that memory without a global blacklist. Failures are counted per
``(strategy, shape)`` where ``shape`` is a cheap, honest description of the
payload ("json", "diff", "traceback", "code", "text") — never a model guess.
After ``disable_after`` consecutive hard failures in one shape the strategy is
skipped for that shape only; another shape keeps trying it. A success resets the
run, so a transient failure does not outlive the condition that caused it.

Only hard failures count: a strategy that raised, returned a non-string, produced
an empty candidate, or whose validator raised. A validator *rejection* is the
validator doing its job and is recorded as an observation, not a failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Iterable

DISABLE_AFTER = 3
MAX_SCOPES = 512
MAX_RECORDS = 10_000

_HARD = frozenset({
    "strategy raised", "candidate is not text", "candidate is empty",
    "validator raised",
})


def shape(text: str) -> str:
    """A cheap, deterministic description of a payload's shape."""
    if not isinstance(text, str) or not text:
        return "empty"
    head = text.lstrip()[:400]
    if head[:1] in ("{", "["):
        return "json"
    if head.startswith("diff --git") or head.startswith("--- ") or head.startswith("+++ "):
        return "diff"
    if "Traceback (most recent call last)" in head:
        return "traceback"
    if "\n    " in head or head.startswith(("def ", "class ", "import ")):
        return "code"
    return "text"


def fingerprint(text: str) -> str:
    """A short content fingerprint, for the record only (never a decision key)."""
    if not isinstance(text, str):
        return ""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class Record:
    """One strategy attempt, with the fields PART 15 asks to record."""

    at: float
    strategy: str
    scope: str
    fingerprint: str
    outcome: str
    accepted: bool
    saved_bytes: int
    elapsed_ms: float
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def is_hard_failure(reason: str) -> bool:
    """Whether a rejection reason counts as a hard failure for tracking."""
    return any(token in reason for token in _HARD)


class Ledger:
    """Bounded in-process failure ledger with optional durable JSONL sink.

    ``path`` appends one JSON object per attempt and never raises: a ledger
    write must not break a tool call. ``max_scopes`` bounds the number of
    distinct ``(strategy, shape)`` keys, ``max_records`` bounds the in-memory
    history used for ``recent``.
    """

    def __init__(self, *, path: str | None = None, disable_after: int = DISABLE_AFTER,
                 max_scopes: int = MAX_SCOPES, max_records: int = MAX_RECORDS):
        self._path = os.path.expanduser(path) if path else None
        self._disable_after = max(1, int(disable_after))
        self._max_scopes = max(1, int(max_scopes))
        self._records: deque[Record] = deque(maxlen=max(1, int(max_records)))
        self._consecutive: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    # -- recording ---------------------------------------------------------
    def record(self, *, strategy: str, scope: str, text_fingerprint: str,
               accepted: bool, saved_bytes: int, elapsed_ms: float,
               reason: str = "") -> Record:
        record = Record(
            at=time.time(), strategy=strategy, scope=scope,
            fingerprint=text_fingerprint,
            outcome=("accepted" if accepted else
                     "failed" if is_hard_failure(reason) else "rejected"),
            accepted=accepted, saved_bytes=saved_bytes,
            elapsed_ms=round(elapsed_ms, 6), reason=reason[:200],
        )
        with self._lock:
            key = (strategy, scope)
            if accepted:
                self._consecutive[key] = 0
            elif record.outcome == "failed":
                self._consecutive[key] = self._consecutive.get(key, 0) + 1
            self._prune_locked()
            self._records.append(record)
        self._append(record)
        return record

    def is_disabled(self, strategy: str, scope: str) -> bool:
        with self._lock:
            return self._consecutive.get((strategy, scope), 0) >= self._disable_after

    def disabled(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                strategy: sorted(s for (s, count) in self._consecutive.items()
                                 if s == strategy and count >= self._disable_after)
                for strategy in {name for name, _ in self._consecutive}
            }

    def reset(self, strategy: str | None = None) -> None:
        """Clear failure counts (all, or one strategy) so it is tried again."""
        with self._lock:
            if strategy is None:
                self._consecutive.clear()
            else:
                for key in [k for k in self._consecutive if k[0] == strategy]:
                    del self._consecutive[key]

    # -- introspection -----------------------------------------------------
    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        with self._lock:
            items = list(self._records)[-max(1, int(limit)):]
        return [item.as_dict() for item in items]

    def stats(self) -> dict[str, object]:
        with self._lock:
            records = list(self._records)
            disabled = {s: sorted(sc for (st, sc), count in self._consecutive.items()
                                  if st == s and count >= self._disable_after)
                        for s in {k[0] for k in self._consecutive}}
        counts = {"accepted": 0, "failed": 0, "rejected": 0}
        for item in records:
            counts[item.outcome] = counts.get(item.outcome, 0) + 1
        return {
            "records": len(records),
            "outcomes": counts,
            "disabled": {k: v for k, v in disabled.items() if v},
            "disable_after": self._disable_after,
        }

    # -- internals ---------------------------------------------------------
    def _prune_locked(self) -> None:
        if len(self._consecutive) <= self._max_scopes:
            return
        # Drop the oldest scopes first; a bounded ledger is better than growth.
        for key in list(self._consecutive)[:len(self._consecutive) - self._max_scopes]:
            del self._consecutive[key]

    def _append(self, record: Record) -> None:
        if not self._path:
            return
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")
        except OSError:
            pass  # a ledger write must never break a tool call


def record_attempts(ledger: Ledger | None, text: str,
                    evaluations: Iterable[object]) -> None:
    """Feed one optimization run's evaluations into a ledger."""
    if ledger is None:
        return
    scope = shape(text)
    digest = fingerprint(text)
    for evaluation in evaluations:
        ledger.record(
            strategy=getattr(evaluation, "name", "unknown"),
            scope=scope, text_fingerprint=digest,
            accepted=bool(getattr(evaluation, "accepted", False)),
            saved_bytes=int(getattr(evaluation, "saved_bytes", 0)),
            elapsed_ms=float(getattr(evaluation, "elapsed_ms", 0.0)),
            reason=str(getattr(evaluation, "reason", "")),
        )
