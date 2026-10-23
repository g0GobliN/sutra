"""tooltrim — deterministic compression of oversized tool output.

Hermes spends most of its tokens re-sending context on every turn, and tool
output is the part that grows without bound. This plugin sits on the
``tool_execution`` middleware path and shrinks a tool result *after* it has run,
before the model ever sees it.

Design rules, in priority order:

1. **Never invent content.** Only remove, collapse, or elide, and always leave a
   marker saying what was removed and how to get it back.
2. **Never touch what a human needs verbatim.** Diffs, patches and tracebacks are
   kept in full; long ones are head+tail trimmed with the error end preserved.
3. **Fail open.** Any unexpected shape, parse error or exception returns the
   original result untouched.
4. **Measurable.** Every compression appends a line to a local stats file so the
   saving can be proved rather than claimed.

Configuration comes from the environment so it can be tuned without a code
change:

  TOOLTRIM_ENABLED      "0" disables the plugin entirely (default "1")
  TOOLTRIM_MAX_BYTES    only results larger than this are touched (default 24576)
  TOOLTRIM_HEAD_BYTES   bytes kept from the start (default 12288)
  TOOLTRIM_TAIL_BYTES   bytes kept from the end (default 6144)
  TOOLTRIM_STATS_FILE   where the saving log lives (default ~/.hermes/cache/tooltrim-stats.jsonl)
  TOOLTRIM_JSON_ITEMS   JSON arrays longer than this are elided in the middle (default 40)
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

__version__ = "0.1.0"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DIFF_RE = re.compile(r"^(diff --git |--- a/|\+\+\+ b/|@@ )", re.M)
_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.M)

_DEFAULT_MAX_BYTES = 24_576
_DEFAULT_HEAD_BYTES = 12_288
_DEFAULT_TAIL_BYTES = 6_144
_DEFAULT_JSON_ITEMS = 40


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return os.environ.get("TOOLTRIM_ENABLED", "1").strip() not in {"0", "false", "off", "no"}


def stats_path() -> str:
    return os.environ.get(
        "TOOLTRIM_STATS_FILE",
        os.path.join(os.path.expanduser("~"), ".hermes", "cache", "tooltrim-stats.jsonl"),
    )


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def collapse_repeats(text: str, keep: int = 2) -> str:
    """Collapse runs of identical consecutive lines, keeping ``keep`` of them."""
    lines = text.splitlines()
    out: list[str] = []
    run_line: str | None = None
    run_len = 0
    for line in lines:
        if line == run_line:
            run_len += 1
            if run_len <= keep:
                out.append(line)
            continue
        if run_len > keep and run_line is not None:
            out.append(f"[tooltrim: {run_len - keep} more identical line(s) omitted]")
        run_line, run_len = line, 1
        out.append(line)
    if run_len > keep and run_line is not None:
        out.append(f"[tooltrim: {run_len - keep} more identical line(s) omitted]")
    return "\n".join(out)


def _elide_middle(text: str, head_bytes: int, tail_bytes: int, reason: str) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= head_bytes + tail_bytes:
        return text
    head = raw[:head_bytes].decode("utf-8", "ignore")
    tail = raw[-tail_bytes:].decode("utf-8", "ignore")
    omitted = len(raw) - head_bytes - tail_bytes
    marker = (
        f"\n… [tooltrim: {omitted:,} bytes elided — {reason}. "
        f"Re-run the tool with a narrower query (grep/head/offset) to see the middle.] …\n"
    )
    return head + marker + tail


def _elide_json_array(text: str, limit: int) -> str | None:
    """Elide the middle of a JSON array that is the whole payload, else None."""
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or len(data) <= limit:
        return None
    keep_head = max(1, limit // 2)
    keep_tail = max(1, limit // 4)
    head = data[:keep_head]
    tail = data[-keep_tail:] if keep_tail else []
    omitted = len(data) - len(head) - len(tail)
    payload = {
        "tooltrim": {
            "omitted_items": omitted,
            "kept_head": len(head),
            "kept_tail": len(tail),
            "original_items": len(data),
        },
        "items": head + [{"tooltrim_elided": f"{omitted} item(s) omitted"}] + tail,
    }
    return json.dumps(payload, ensure_ascii=False)


def compress(text: str, *, max_bytes: int | None = None, head_bytes: int | None = None,
             tail_bytes: int | None = None, json_items: int | None = None) -> tuple[str, dict[str, Any]]:
    """Compress one tool result. Returns (possibly new text, stats)."""
    max_bytes = max_bytes or _env_int("TOOLTRIM_MAX_BYTES", _DEFAULT_MAX_BYTES)
    head_bytes = head_bytes or _env_int("TOOLTRIM_HEAD_BYTES", _DEFAULT_HEAD_BYTES)
    tail_bytes = tail_bytes or _env_int("TOOLTRIM_TAIL_BYTES", _DEFAULT_TAIL_BYTES)
    json_items = json_items or _env_int("TOOLTRIM_JSON_ITEMS", _DEFAULT_JSON_ITEMS)

    original_bytes = len(text.encode("utf-8", "replace"))
    stats: dict[str, Any] = {"original_bytes": original_bytes, "compressed_bytes": original_bytes,
                             "changed": False, "rule": "none"}
    if original_bytes <= max_bytes:
        return text, stats

    work = strip_ansi(text)

    if _DIFF_RE.search(work):
        stats["rule"] = "diff-kept"
        return text, stats

    elided = _elide_json_array(work, json_items)
    if elided is not None:
        stats.update(compressed_bytes=len(elided.encode("utf-8")), changed=True, rule="json-array")
        return elided, stats

    if _TRACEBACK_RE.search(work):
        out = _elide_middle(work, 2_048, 8_192, "traceback kept, middle elided")
        stats.update(compressed_bytes=len(out.encode("utf-8")), changed=True, rule="traceback")
        return out, stats

    collapsed = collapse_repeats(work)
    out = _elide_middle(collapsed, head_bytes, tail_bytes, "large output")
    if out == text:
        return text, stats
    stats.update(compressed_bytes=len(out.encode("utf-8")), changed=True,
                 rule="head-tail" if collapsed == work else "head-tail+dedupe")
    return out, stats


_TEXT_KEYS = ("output", "stdout", "stderr", "content", "text", "result", "data")


def compress_result(result: Any) -> Any:
    """Compress a tool result of any common shape without changing its type."""
    if not enabled():
        return result
    if isinstance(result, str):
        out, stats = compress(result)
        if stats["changed"]:
            _record(stats)
        return out
    if isinstance(result, dict):
        changed = False
        new = dict(result)
        for key in _TEXT_KEYS:
            value = new.get(key)
            if isinstance(value, str):
                out, stats = compress(value)
                if stats["changed"]:
                    new[key] = out
                    changed = True
                    _record(stats)
        return new if changed else result
    return result


def _record(stats: dict[str, Any]) -> None:
    """Append one saving record. Best-effort: never break a tool call."""
    try:
        path = stats_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "rule": stats["rule"],
            "original_bytes": stats["original_bytes"],
            "compressed_bytes": stats["compressed_bytes"],
            "saved_bytes": max(0, stats["original_bytes"] - stats["compressed_bytes"]),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def tool_execution(*, next_call, tool_name: str | None = None, args: Any = None, **_context: Any) -> Any:
    """The middleware frame: run the tool, then compress what comes back."""
    result = next_call(args)
    try:
        return compress_result(result)
    except Exception:  # fail open: an exotic result shape is never worth a broken call
        return result


def register(ctx) -> None:
    ctx.register_middleware("tool_execution", tool_execution)
