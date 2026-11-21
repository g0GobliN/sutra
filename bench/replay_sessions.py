#!/usr/bin/env python3
"""Replay real host conversations through the request-stage policy.

``bench/request_stage.py`` measures what the policy costs on a synthetic
conversation, and ``bench/replay_requests.py`` replays request bodies a host
recorded when a call failed. Both leave one question open: what does the policy
remove from ordinary sessions, the ones that never failed and were never saved
as a request dump?

This script answers it from the host's own session store. It opens a Hermes
``state.db`` read-only, rebuilds each conversation from its active messages,
and runs every mode through the same policy — reporting removed bytes, latency
and the two safety facts ``replay_requests`` already audits:

* every message the policy touched still carries an id whose stored copy is
  byte-identical to the original, and
* every message it did not touch is byte-identical to what the session holds.

The database is read, never written. Nothing is uploaded, and the report
contains counts and byte sizes only, never message text.

Run it from the repository root::

    python3 bench/replay_sessions.py
    python3 bench/replay_sessions.py --db ~/.hermes/state.db --limit 40 --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path.home() / ".hermes" / "state.db"
_CONVERSATION_ROLES = ("system", "user", "assistant", "tool")


def load_plugin(name: str):
    """Import a plugin submodule the way the host imports the package."""
    if "sutra_plugin" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "sutra_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["sutra_plugin"] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"sutra_plugin.{name}")


def load_replay():
    """Import ``bench/replay_requests.py`` for its policy helpers and audits."""
    path = ROOT / "bench" / "replay_requests.py"
    spec = importlib.util.spec_from_file_location("bench_replay_requests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_replay_requests"] = module
    spec.loader.exec_module(module)
    return module


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def session_ids(connection: sqlite3.Connection, *, limit: int, min_messages: int) -> list[str]:
    """Recent sessions with enough active messages to be worth replaying."""
    rows = connection.execute(
        """
        SELECT session_id, COUNT(*) AS total, MAX(timestamp) AS newest
          FROM messages
         WHERE active = 1 AND compacted = 0 AND content IS NOT NULL
           AND role IN (?, ?, ?, ?)
         GROUP BY session_id
        HAVING total >= ?
         ORDER BY newest DESC
         LIMIT ?
        """,
        (*_CONVERSATION_ROLES, int(min_messages), int(limit)),
    ).fetchall()
    return [row["session_id"] for row in rows]


def conversation(connection: sqlite3.Connection, session_id: str) -> list[dict]:
    """Rebuild the message list the middleware would see for one session."""
    rows = connection.execute(
        """
        SELECT role, content
          FROM messages
         WHERE session_id = ? AND active = 1 AND compacted = 0
           AND content IS NOT NULL AND role IN (?, ?, ?, ?)
         ORDER BY id
        """,
        (session_id, *_CONVERSATION_ROLES),
    ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def tool_sizes(connection: sqlite3.Connection, *, limit_sessions: int = 200) -> dict:
    """Distribution of stored tool result sizes, in bytes."""
    rows = connection.execute(
        """
        SELECT length(content) AS size
          FROM messages
         WHERE role = 'tool' AND content IS NOT NULL AND active = 1
         ORDER BY id DESC
         LIMIT 20000
        """
    ).fetchall()
    sizes = sorted(int(row["size"]) for row in rows)
    if not sizes:
        return {"count": 0}
    total = sum(sizes)
    return {
        "count": len(sizes),
        "median": int(statistics.median(sizes)),
        "over_2048": sum(1 for size in sizes if size >= 2048),
        "over_8192": sum(1 for size in sizes if size >= 8192),
        "over_32768": sum(1 for size in sizes if size >= 32768),
        "bytes": total,
        "bytes_over_2048": sum(size for size in sizes if size >= 2048),
        "bytes_over_8192": sum(size for size in sizes if size >= 8192),
    }


def audit(prompt, messages, request_after, touched, handle, replay_requests) -> tuple[bool, int, bool]:
    """Check recovery for touched messages and identity for the rest.

    A touched message either carries an id retained by this run, or it arrived
    already carrying one (a result an earlier stage compressed). The second
    kind is counted as ``declared`` rather than failed: its bytes live in the
    host's store, which this read-only bench does not open.
    """
    if request_after is None:
        return True, 0, True
    text = replay_requests.content_text
    updated = request_after["messages"]
    touched = set(touched)
    recoverable = True
    untouched = True
    declared = 0
    for index, (old, new) in enumerate(zip(messages, updated)):
        if index in touched:
            ids = prompt._recovery_ids(text(new))
            if not ids:
                recoverable = False
                continue
            retained = next((handle.get(entry_id) for entry_id in ids
                             if handle.get(entry_id).status == "ok"), None)
            if retained is not None:
                if handle.read_bytes(retained.entry) != text(old).encode():
                    recoverable = False
            elif ids & prompt._recovery_ids(text(old)):
                declared += 1
            else:
                recoverable = False
        elif text(old) != text(new):
            untouched = False
    return recoverable, declared, untouched


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Hermes state.db (read-only)")
    parser.add_argument("--limit", type=int, default=40, help="sessions to replay, newest first")
    parser.add_argument("--min-messages", type=int, default=10, help="skip shorter sessions")
    parser.add_argument("--modes", default="balanced,aggressive,beast")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    database = Path(args.db)
    if not database.is_file():
        print(f"replay: no database at {database}", file=sys.stderr)
        return 1

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    prompt = load_plugin("prompt")
    store_module = load_plugin("store")
    replay_requests = load_replay()

    connection = connect(database)
    try:
        selected = session_ids(connection, limit=args.limit, min_messages=args.min_messages)
        distribution = tool_sizes(connection)
        conversations = [conversation(connection, sid) for sid in selected]
    finally:
        connection.close()

    rows: list[dict] = []
    summary: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="sutra-replay-sessions-") as directory:
        handle = store_module.Store(
            store_module.StoreConfig(directory=str(Path(directory) / "originals")))
        for mode in modes:
            saved = [0]
            before_total = [0]
            timings: list[float] = []
            sessions = 0
            declared_total = [0]
            recovered = True
            untouched = True
            for sid, messages in zip(selected, conversations):
                started = time.perf_counter()
                decision = replay_requests.replay(messages, mode, handle)
                timings.append((time.perf_counter() - started) * 1000.0)
                saved[0] += decision["before"] - decision["after"]
                before_total[0] += decision["before"]
                sessions += 1
                row_recovered, row_declared, row_untouched = audit(
                    prompt, messages, decision["request"], decision["touched"], handle,
                    replay_requests)
                declared_total[0] += row_declared
                recovered = recovered and row_recovered
                untouched = untouched and row_untouched
                rows.append({
                    "session": sid,
                    "mode": mode,
                    "messages": len(messages),
                    "before": decision["before"],
                    "after": decision["after"],
                    "saved": decision["before"] - decision["after"],
                    "elided": decision["elided"],
                    "recovered": row_recovered,
                    "untouched": row_untouched,
                    "ms": round(timings[-1], 2),
                })
            pct = round(100.0 * saved[0] / before_total[0], 1) if before_total[0] else 0.0
            summary.append({
                "mode": mode,
                "sessions": sessions,
                "before": before_total[0],
                "saved": saved[0],
                "saved_pct": pct,
                "median_ms": round(statistics.median(timings), 2) if timings else 0.0,
                "p95_ms": round(sorted(timings)[max(0, int(len(timings) * 0.95) - 1)], 2)
                if timings else 0.0,
                "recovered": recovered,
                "declared": declared_total[0],
                "untouched": untouched,
            })

    report = {"tool_results": distribution, "summary": summary, "rows": rows}
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"replay: {len(selected)} sessions from {database}")
    if distribution["count"]:
        print(
            f"  tool results: {distribution['count']:,} stored, median "
            f"{distribution['median']:,} B, "
            f"{distribution['over_8192']:,} at or over 8,192 B"
        )
    if not selected:
        print("  no session with enough active messages")
        return 0
    for entry in summary:
        print(
            f"  {entry['mode']:<10} {entry['before']:>9,} B -> "
            f"{entry['before'] - entry['saved']:>9,} B  "
            f"saved {entry['saved']:>8,} ({entry['saved_pct']:>4}%)  "
            f"median {entry['median_ms']:>6} ms  "
            f"recovered={'yes' if entry['recovered'] else 'NO'} "
            f"({entry['declared']:,} already marked)  "
            f"untouched={'yes' if entry['untouched'] else 'NO'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
