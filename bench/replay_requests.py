#!/usr/bin/env python3
"""Replay recorded request bodies through the request-stage policy.

``bench/request_stage.py`` measures synthetic conversations, so it says what the
policy costs but not what it finds. This script answers the second question: it
reads request bodies a host recorded before submission, rebuilds the ``messages``
list the middleware would see, and reports what each mode removes — together
with two safety facts that matter more than the bytes:

* every message the policy touched still carries an id whose stored copy is
  byte-identical to the original, and
* every message it did not touch is byte-identical to what was recorded.

The recorded bodies are not part of this repository. Point the script at a
directory of JSON files shaped like ``{"request": {"body": {"messages": [...]}}}``
(Hermes writes these under ``$HERMES_HOME/sessions`` when a request fails) or at
a single file. Nothing is uploaded, and the report contains counts and byte
sizes only, never message text.

Run it from the repository root::

    python3 bench/replay_requests.py --source ~/.hermes/sessions
    python3 bench/replay_requests.py --source request.json --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MODULE = "sutra_plugin"


def load(name: str):
    """Import a plugin submodule the way the host imports the package."""
    if _MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _MODULE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{_MODULE}.{name}")


def payload_bytes(messages) -> int:
    """Bytes of conversation text, counting every text part of a block list."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content.encode())
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += len(part["text"].encode())
    return total


def content_text(message) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def recorded_messages(path: Path):
    """Return the message list a recorded request carried, or None."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    body = document.get("request", document).get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return None
    if not isinstance(body, dict):
        return None
    messages = body.get("messages") or body.get("input")
    if not isinstance(messages, list) or not messages:
        return None
    return messages


def replay(messages, mode: str, handle) -> dict:
    prompt = load("prompt")
    config = load("config")
    caps = config.PROMPT_CAPS_BY_MODE[mode]
    before = payload_bytes(messages)
    started = time.perf_counter()
    decision = prompt.prepare(
        {"model": "replay", "messages": messages},
        keep_last=prompt.DEFAULT_KEEP_LAST,
        min_message_bytes=caps["min_message_bytes"],
        min_request_bytes=caps["min_request_bytes"],
        max_messages=caps["max_messages"],
        head_bytes=prompt.HEAD_BYTES if caps["preview"] else 0,
        tail_bytes=prompt.TAIL_BYTES if caps["preview"] else 0,
        retain=prompt.host_retainer(store=handle),
    )
    elapsed = (time.perf_counter() - started) * 1000
    after = before if decision.request is None else payload_bytes(decision.request["messages"])
    recoverable, checked, untouched = audit(prompt, messages, decision, handle)
    return {
        "mode": mode,
        "messages": len(messages),
        "results": sum(1 for message in messages if message.get("role") == "tool"),
        "before": before,
        "after": after,
        "saved": before - after,
        "elided": decision.elided,
        "ms": round(elapsed, 2),
        "recoverable": recoverable,
        "checked": checked,
        "untouched": untouched,
        "request": decision.request,
        "touched": list(decision.messages),
    }


def audit(prompt, messages, decision, handle) -> tuple[bool, int, bool]:
    """Check recovery for touched messages and identity for the rest."""
    if decision.request is None:
        return True, 0, True
    updated = decision.request["messages"]
    touched = set(decision.messages)
    checked = 0
    recoverable = True
    untouched = True
    for index, (old, new) in enumerate(zip(messages, updated)):
        if index in touched:
            ids = prompt._recovery_ids(content_text(new))
            if not ids:
                recoverable = False
                continue
            lookup = handle.get(next(iter(ids)))
            checked += 1
            if lookup.status != "ok":
                recoverable = False
                continue
            if handle.read_bytes(lookup.entry) != content_text(old).encode():
                recoverable = False
        elif content_text(old) != content_text(new):
            untouched = False
    return recoverable, checked, untouched


def sources(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(source.glob("*.json"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path,
                        help="a recorded request file, or a directory of them")
    parser.add_argument("--modes", default="balanced,aggressive,beast")
    parser.add_argument("--json", action="store_true", help="print rows as JSON")
    args = parser.parse_args(argv)

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    store = load("store")
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        handle = store.Store(store.StoreConfig(directory=directory))
        for path in sources(args.source):
            messages = recorded_messages(path)
            if messages is None:
                continue
            for mode in modes:
                row = replay(messages, mode, handle)
                row["source"] = path.name
                rows.append(row)

    summary = []
    for mode in modes:
        group = [row for row in rows if row["mode"] == mode]
        if not group:
            continue
        before = sum(row["before"] for row in group)
        saved = sum(row["saved"] for row in group)
        summary.append({
            "mode": mode,
            "files": len(group),
            "bytes": before,
            "saved": saved,
            "saved_pct": round(100 * saved / max(1, before), 1),
            "median_ms": round(statistics.median(row["ms"] for row in group), 2),
            "recoverable": all(row["recoverable"] for row in group),
            "untouched": all(row["untouched"] for row in group),
        })

    if args.json:
        print(json.dumps({"rows": rows, "summary": summary}, indent=2, sort_keys=True))
    else:
        for row in rows:
            share = 100 * row["saved"] / max(1, row["before"])
            print(f"{row['source'][:44]:44} msgs={row['messages']:3} results={row['results']:2} "
                  f"{row['mode']:10} {row['before']:7} -> {row['after']:7} "
                  f"saved={row['saved']:6} ({share:5.1f}%) elided={row['elided']:2} "
                  f"{row['ms']:7.2f}ms recoverable={row['recoverable']} "
                  f"checked={row['checked']:2} untouched={row['untouched']}")

        for item in summary:
            print(f"{item['mode']:10} files={item['files']:3} bytes={item['bytes']:8} "
                  f"saved={item['saved']:8} ({item['saved_pct']:5.1f}%) "
                  f"median={item['median_ms']:6.2f}ms "
                  f"recoverable={item['recoverable']} untouched={item['untouched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
