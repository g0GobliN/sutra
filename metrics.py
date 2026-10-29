"""Operational telemetry: one owner for the counters and the savings log.

Compression decisions, restores, cleanup and store usage are recorded here.
``snapshot()`` is the queryable surface over the process-scoped counters;
``record_compression()`` also appends rewrites to the durable JSONL savings log
from the same normalised record. There is one compression schema, not a second
log format that the counters cannot describe.

Per-process and best-effort by construction: every entry point catches its own
failure, because a metric is never worth a failed tool call.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_SCOPE_NOTE = ("Counters cover this Hermes plugin-host process only; they reset on "
               "restart. The JSONL stats log is durable and retains rewritten "
               "results only.")

# Never follow a symlink planted at the stats path: appending JSON through one
# writes to whatever it points at.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

_LOCK = threading.Lock()
_RESTORES: dict[str, int] = {}
_CLEANUP = {
    "runs": 0,
    "entries": 0,
    "bytes": 0,
    "expired": 0,
    "quota": 0,
    "orphans": 0,
    "temp": 0,
}
_COMPRESSION: dict[str, Any] = {
    "total": 0,
    "changed": 0,
    "unchanged": 0,
    "rules": {},
    "original_bytes": 0,
    "compressed_bytes": 0,
    "saved_bytes": 0,
    "omitted_bytes": 0,
    "partial": 0,
    "stored": 0,
    "fail_open": 0,
    "duration_ms_total": 0.0,
}


def _reason(response: dict[str, Any]) -> str:
    """Name one restore outcome for the counters.

    The response status is the primary signal; only the generic ``error``
    status needs its message inspected to separate a bad id from a bad range.
    """
    status = str(response.get("status") or "error")
    if status == "ok":
        return "ok"
    if status in {"not_found", "expired", "unavailable", "too_large"}:
        return status
    error = str(response.get("error") or "").lower()
    if "invalid id" in error or "missing required argument" in error:
        return "bad_id"
    if "range" in error:
        return "range"
    return "bad_request"


def record_restore(response: dict[str, Any]) -> None:
    """Count one restore response. Never raises."""
    try:
        reason = _reason(response)
        with _LOCK:
            _RESTORES[reason] = _RESTORES.get(reason, 0) + 1
        if reason != "ok":
            logger.info("sutra: restore %s", reason)
    except Exception:  # a metric must never break the tool call
        logger.debug("sutra: could not record the restore metric", exc_info=True)


def record_cleanup(report: dict[str, int]) -> None:
    """Add one prune report to the cleanup totals. Never raises."""
    try:
        removed = sum(int(report.get(key, 0) or 0)
                      for key in ("expired", "quota", "orphans", "temp"))
        if removed <= 0:
            return
        freed = int(report.get("bytes", 0) or 0)
        with _LOCK:
            _CLEANUP["runs"] += 1
            _CLEANUP["entries"] += removed
            _CLEANUP["bytes"] += freed
            for key in ("expired", "quota", "orphans", "temp"):
                _CLEANUP[key] += int(report.get(key, 0) or 0)
        logger.info(
            "sutra: cleanup removed %d entries (%d bytes): %d expired, "
            "%d quota, %d orphan, %d temp",
            removed, freed, int(report.get("expired", 0) or 0),
            int(report.get("quota", 0) or 0), int(report.get("orphans", 0) or 0),
            int(report.get("temp", 0) or 0))
    except Exception:
        logger.debug("sutra: could not record the cleanup metric", exc_info=True)


def _rotate_stats(path: str, limit: int) -> None:
    """Keep one previous log once the current one reaches ``limit``."""
    if limit <= 0:
        return
    try:
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size < limit:
            return
        os.replace(path, path + ".1")
    except OSError:
        pass


def _compression_record(stats: dict[str, Any]) -> dict[str, Any]:
    """One durable record; this is the only definition of the JSONL schema."""
    original = max(0, int(stats.get("original_bytes") or 0))
    compressed = max(0, int(stats.get("compressed_bytes") or 0))
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": str(stats.get("tool") or "")[:64],
        "rule": str(stats.get("rule") or "unknown")[:64],
        "changed": bool(stats.get("changed")),
        "original_bytes": original,
        "compressed_bytes": compressed,
        "saved_bytes": max(0, original - compressed),
        "omitted_bytes": max(0, int(stats.get("omitted_bytes") or 0)),
        "partial": bool(stats.get("partial")),
        "stored": bool(stats.get("stored")),
        "id": stats.get("id"),
        "duration_ms": stats.get("duration_ms"),
        "fail_open": bool(stats.get("fail_open")),
    }


def _note_compression(record: dict[str, Any]) -> None:
    """Add one already-normalised compression record to the counters."""
    changed = bool(record["changed"])
    rule = str(record.get("rule") or "unknown")[:64]
    with _LOCK:
        _COMPRESSION["total"] += 1
        _COMPRESSION["changed"] += int(changed)
        _COMPRESSION["unchanged"] += int(not changed)
        _COMPRESSION["original_bytes"] += int(record["original_bytes"])
        _COMPRESSION["compressed_bytes"] += int(record["compressed_bytes"])
        _COMPRESSION["saved_bytes"] += int(record["saved_bytes"])
        _COMPRESSION["omitted_bytes"] += int(record["omitted_bytes"])
        _COMPRESSION["partial"] += int(bool(record["partial"]))
        _COMPRESSION["stored"] += int(bool(record["stored"]))
        _COMPRESSION["fail_open"] += int(bool(record["fail_open"]))
        _COMPRESSION["duration_ms_total"] += float(record.get("duration_ms") or 0)
        rules = _COMPRESSION["rules"]
        rules[rule] = rules.get(rule, 0) + 1


def record_compression(stats: dict[str, Any], cfg: Any) -> None:
    """Count one compression decision; append rewrites to the durable log.

    The in-memory counters and the durable log consume one normalised record;
    unchanged results stay in the counters because the log is a savings log.
    """
    try:
        record = _compression_record(stats)
    except Exception:  # a metric must never break the tool call
        logger.debug("sutra: could not normalise the compression metric", exc_info=True)
        return
    try:
        _note_compression(record)
    except Exception:
        logger.debug("sutra: could not count the compression metric", exc_info=True)
    if not record["changed"]:
        return
    try:
        path = cfg.stats_path
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        _rotate_stats(path, int(cfg.stats_max_bytes))
        payload = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_NOFOLLOW, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
    except OSError:
        pass
    except Exception:
        logger.debug("sutra: could not append the compression record", exc_info=True)


def snapshot(store: Any = None) -> dict[str, Any]:
    """One process-scoped surface for compression, restores, cleanup and store size."""
    try:
        with _LOCK:
            restores = {"total": sum(_RESTORES.values()), **dict(_RESTORES)}
            cleanup = dict(_CLEANUP)
            compression = dict(_COMPRESSION)
            compression["rules"] = dict(_COMPRESSION["rules"])
    except Exception:
        compression = {"total": 0, "changed": 0, "unchanged": 0, "rules": {},
                       "original_bytes": 0, "compressed_bytes": 0, "saved_bytes": 0,
                       "omitted_bytes": 0, "partial": 0, "stored": 0, "fail_open": 0,
                       "duration_ms_total": 0.0}
        return {"scope": "process", "scope_note": _SCOPE_NOTE,
                "compression": compression, "restores": {"total": 0}, "cleanup": {}}
    compression["duration_ms_total"] = round(float(compression["duration_ms_total"]), 2)
    data: dict[str, Any] = {
        "scope": "process",
        "scope_note": _SCOPE_NOTE,
        "restores": restores,
        "cleanup": cleanup,
        "compression": compression,
    }
    if store is not None:
        try:
            entries, size = store.usage()
            data["store"] = {"entries": entries, "bytes": size,
                             "enabled": bool(store.config.enabled)}
        except Exception:
            data["store"] = {"entries": None, "bytes": None,
                             "enabled": bool(getattr(store.config, "enabled", False))}
    return data
