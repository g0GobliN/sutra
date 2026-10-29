"""Classification and deterministic compression policies.

Pure functions: no storage, no Hermes imports, no model calls. The plugin
adapter in ``__init__`` adds the original-output store, metrics and wiring.

Design rules, in priority order:

1. Never invent content. Only remove, collapse or elide, and always leave a
   marker saying what was removed, how much, and how to get it back.
2. Never change a payload's own format: a JSON array stays an array, a diff is
   returned byte-identical, a traceback keeps its error end.
3. Fail open. Any unexpected shape, parse error or exception returns the
   original text.
4. Be measurable. Every decision reports bytes in, bytes out, and the rule.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from . import config

logger = logging.getLogger(__name__)

# --- kinds of payload the classifier can name ------------------------------

KIND_EMPTY = "empty"
KIND_PATCH = "patch"
KIND_JSON = "json"
KIND_TRACEBACK = "traceback"
KIND_TEXT = "text"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Any of these anywhere means "this payload contains a patch": protect all of it.
_DIFF_RE = re.compile(r"^(diff --git |--- a/|\+\+\+ b/|@@ )", re.M)
_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.M)
# `diff a b` (non-unified) writes change commands like "2c2" plus < / > rows.
_CHANGE_CMD_RE = re.compile(r"^[0-9]+(?:,[0-9]+)?[acd][0-9]+(?:,[0-9]+)?$", re.M)
_LT_RE = re.compile(r"^< ", re.M)
_GT_RE = re.compile(r"^> ", re.M)

_DEFAULT_MAX_BYTES = config.DEFAULT_MAX_BYTES
_DEFAULT_HEAD_BYTES = config.DEFAULT_HEAD_BYTES
_DEFAULT_TAIL_BYTES = config.DEFAULT_TAIL_BYTES
_JSON_MAX_DEPTH = 12
_MAX_INPUT_BYTES = 64 * 1024 * 1024  # refuse to rewrite anything larger
_TRACEBACK_TAIL_MIN = 8_192
_TRACEBACK_HEAD_MAX = 2_048
_MARKER_BUDGET = 1_024  # bytes reserved for markers inside max_bytes
_MAX_FIELD_PATH = config.MAX_FIELD_POINTER  # UTF-8 bytes, shared with recovery
# Characters that cannot be quoted in a marker attribute or the suggested call
# without changing its meaning (quote/backslash), breaking the line or the
# bracketed wrapper (newline, carriage return, right bracket), truncating the
# hint when a caller extracts it (right parenthesis), or confusing a shell
# (NUL). A field with one of these in its pointer is left uncompressed.
_UNQUOTABLE_FIELD_CHARS = frozenset('"\\\n\r])\x00')

_json_items_warned = False


@dataclass(frozen=True)
class Ref:
    """A retained original, as quoted in an elision marker.

    ``field`` is set when the elision happened inside one JSON string field of
    the payload: the byte offsets then belong to that field, not the payload,
    so the marker must not quote them.
    """

    id: str
    ttl_hours: int = 0
    field: str | None = None


@dataclass(frozen=True)
class Classification:
    kind: str
    document: Any = None


@dataclass(frozen=True)
class Limits:
    max_bytes: int
    head_bytes: int
    tail_bytes: int


def config_from_env() -> Limits:
    """The engine's view of the one resolved configuration."""
    cfg = config.resolve()
    return Limits(cfg.max_bytes, cfg.head_bytes, cfg.tail_bytes)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _looks_like_patch(text: str) -> bool:
    """True when the payload *is* a patch (or contains one) and must not move."""
    if _DIFF_RE.search(text):
        return True
    # Unified diff without git prefixes: the first two non-blank lines are the
    # file headers. `git diff --no-prefix` and plain `diff -u` land here.
    lines = [line for line in text.splitlines() if line.strip()][:2]
    if len(lines) == 2 and lines[0].startswith("--- ") and lines[1].startswith("+++ "):
        return True
    # Plain `diff a b`: a change command plus removed and added rows.
    return bool(_CHANGE_CMD_RE.search(text) and _LT_RE.search(text) and _GT_RE.search(text))


def _parse_json_document(text: str) -> Any | None:
    """Return the parsed document if ``text`` is one JSON object/array, else None."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError, RecursionError):
        return None


def _parses_as_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (ValueError, TypeError, RecursionError):
        return False


def classify(text: str) -> Classification:
    """Name the payload's kind. Conservative: unknown text is ``text``."""
    return _classify_work(strip_ansi(text))


def _classify_work(work: str) -> Classification:
    if not work or not work.strip():
        return Classification(KIND_EMPTY)
    if _looks_like_patch(work):
        return Classification(KIND_PATCH)
    document = _parse_json_document(work)
    if document is not None:
        return Classification(KIND_JSON, document)
    if _TRACEBACK_RE.search(work):
        return Classification(KIND_TRACEBACK)
    return Classification(KIND_TEXT)


# --- markers ---------------------------------------------------------------

def _marker_line(omitted: int, reason: str, ref: Ref | None, *, total: int,
                 start: int | None = None, end: int | None = None) -> str:
    """One line the model can act on: what is missing, whether it is stored, how to get it."""
    head = f"[sutra: partial · {omitted:,} bytes elided ({reason})"
    if ref is None:
        return (head + " · original not retained · re-run the tool with a narrower "
                "query (grep/head/offset) to see the middle]")
    kept = f"original {total:,} B retained"
    if ref.ttl_hours:
        kept += f" {ref.ttl_hours}h"
    # Reads like the documented grammar: "(reason) · field=… · id=…". The
    # scope carries its own separator because it is optional; a missing field
    # must not leave an empty segment behind ("· · id=").
    scope = f' · field="{ref.field}"' if ref.field else ""
    call_scope = f', field="{ref.field}"' if ref.field else ""
    return (head + f"{scope} · id={ref.id} · {kept}"
            f' · retrieve: sutra_restore(id="{ref.id}"{call_scope}, mode="bytes", '
            f"start={start}, end={end})]")


def _limits(max_bytes: int, head_bytes: int, tail_bytes: int,
            reserve: int = _MARKER_BUDGET) -> tuple[int, int]:
    """Clamp head/tail so the whole rewrite, marker included, fits max_bytes.

    When the configured head and tail together exceed the budget they are scaled
    down proportionally rather than letting the head consume all of it: the end
    of a log is usually the part that says what happened.

    ``reserve`` is the room left for the marker. One document splits its budget
    between several fields, so each field passes the share its own marker gets.
    """
    budget = max_bytes - max(0, reserve)
    if budget < 64:
        return 0, 0
    head = max(0, int(head_bytes))
    tail = max(0, int(tail_bytes))
    if head + tail <= budget:
        return head, tail
    configured = head + tail
    scaled_head = int(budget * head / configured)
    scaled_tail = budget - scaled_head
    return scaled_head, scaled_tail


def _traceback_limits(max_bytes: int, head_bytes: int, tail_bytes: int,
                      reserve: int = _MARKER_BUDGET) -> tuple[int, int]:
    """The error end is the point of a traceback: keep at least 1 KiB of it."""
    budget = max_bytes - max(0, reserve)
    if budget < 64:
        return 0, 0
    tail = min(max(tail_bytes, _TRACEBACK_TAIL_MIN), budget)
    if tail < 1_024:
        tail = min(1_024, budget)
    head = max(0, min(head_bytes, _TRACEBACK_HEAD_MAX, budget - tail))
    return head, tail


# --- plain text ------------------------------------------------------------

# `str.splitlines()` (what sutra_restore uses) breaks on these; `io.StringIO`
# (what collapse_repeats iterates) breaks only on "\n". A line number is only
# quoted when the two views agree.
_EXOTIC_LINE_BREAKS = frozenset("\v\f\x1c\x1d\x1e\x85\u2028\u2029")
_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
_LONE_CR_RE = re.compile(r"\r(?!\n)")
# Bytes that split lines for `str.splitlines()` but not for `io.StringIO`.
# The fast path needs none of them before the fragment, or it must decode.
_BYTES_NOT_ONLY_NEWLINE_RE = re.compile(
    rb"[\x0b\x0c\x1c-\x1e]|\xc2\x85|\xe2\x80[\xa8\xa9]|\r(?!\n)")


def _line_numbers_addressable(text: str) -> bool:
    """True when a line number in ``text`` means the same line to restore."""
    if _EXOTIC_LINE_BREAKS.intersection(text):
        return False
    return _LONE_CR_RE.search(text) is None


def _line_base(raw: bytes, offset: int) -> int:
    """0-based line number of the line containing byte ``offset``.

    With only LF/CRLF breaks, each byte of ``\n`` before the offset is one
    complete line, so no decode of the omitted middle is needed. Exotic breaks
    (form feed, NEL, U+2028, a lone CR) make the prefix's `splitlines()` count
    the only correct one, and are rare enough to pay for.
    """
    if offset <= 0:
        return 0
    if _BYTES_NOT_ONLY_NEWLINE_RE.search(raw, 0, offset) is None:
        return raw.count(b"\n", 0, offset)
    prefix = raw[:offset].decode("utf-8", "replace")
    count = len(prefix.splitlines())
    return count if prefix[-1] in _LINE_BREAKS else count - 1


def _dedupe_marker(count: int, ref: Ref | None, line_no: int | None = None) -> str:
    """One marker for a run of removed identical lines.

    ``line_no`` is the first omitted line in the retained scope (1-based), so
    the hint asks for exactly the omitted range in ``mode=lines``; ``end`` is
    exclusive. Line numbers are the one locator that survives ANSI stripping
    and does not need an offset into a re-encoded document.

    When the scope's line breaks are not addressable, the marker says so and
    quotes no call: a hint that cannot point at the omitted bytes is worse than
    a plain statement that they cannot be paged by line.
    """
    if ref is None:
        return f"[sutra: {count:,} more identical line(s) omitted]"
    field = f' · field="{ref.field}"' if ref.field else ""
    if line_no is not None:
        call_scope = f', field="{ref.field}"' if ref.field else ""
        return (f"[sutra: {count:,} identical line(s) omitted{field} · id={ref.id}"
                f' · retrieve: sutra_restore(id="{ref.id}"{call_scope}, '
                f'mode="lines", start={line_no}, end={line_no + count})]')
    return (f"[sutra: {count:,} identical line(s) omitted{field} · id={ref.id}"
            f" · the omitted lines cannot be addressed by line number in this "
            f"result]")


def collapse_repeats(text: str, keep: int = 2, ref: Ref | None = None,
                     line_base: int = 0, line_numbers: bool = True) -> str:
    """Collapse runs of identical consecutive lines, keeping ``keep`` of them.

    Line terminators (LF, CRLF, or none on the last line) are preserved: only
    whole lines are removed and the marker is inserted on its own line.

    ``line_base`` is the 0-based line number at which ``text`` starts in the
    scope the marker's id refers to: 0 for a whole result or a JSON field, the
    preceding line count for a tail fragment. It only affects the hint.
    """
    keep = max(1, keep)
    if not text:
        return text
    nl = "\r\n" if "\r\n" in text else "\n"
    out: list[str] = []
    run_line: str | None = None
    run_len = 0
    run_term = nl
    run_start = 0
    line_no = 0
    addressable = (ref is not None and line_numbers
                   and _line_numbers_addressable(text))
    for line in io.StringIO(text):
        line_no += 1
        body = line.rstrip("\r\n")
        term = line[len(body):]
        if body == run_line:
            run_len += 1
            run_term = term or run_term
            if run_len <= keep:
                out.append(line)
            continue
        if run_line is not None and run_len > keep:
            first = line_base + run_start + keep if addressable else None
            out.append(_dedupe_marker(run_len - keep, ref, first) + run_term)
        run_line, run_len = body, 1
        run_start = line_no
        run_term = term or nl
        out.append(line)
    if run_line is not None and run_len > keep:
        first = line_base + run_start + keep if addressable else None
        out.append(_dedupe_marker(run_len - keep, ref, first) + run_term)
    return "".join(out)


def _utf8_head_end(raw: bytes, end: int) -> int:
    """Move a head cut back to the nearest character boundary.

    ``raw`` is valid UTF-8, so only the character containing ``end`` can be
    split. When ``raw[end]`` is a continuation byte, back up over that
    character and exclude it: the prefix then decodes exactly.
    """
    if end >= len(raw):
        return len(raw)
    while end > 0 and (raw[end] & 0xC0) == 0x80:
        end -= 1
    return max(0, end)


def _utf8_tail_start(raw: bytes, start: int) -> int:
    """Move a tail cut forward to the nearest character boundary."""
    while start < len(raw) and (raw[start] & 0xC0) == 0x80:
        start += 1
    return start


def _elide_middle(text: str, head_bytes: int, tail_bytes: int, reason: str,
                  ref: Ref | None = None) -> tuple[str, int, bool]:
    """Keep the start and the end, replace the middle with an explicit marker.

    The slicing happens on ``text`` itself, so the byte offsets in the marker
    always address the bytes that were stored — never a normalised copy.
    ANSI stripping and repeat collapsing are applied to the kept head and tail
    only, after the offsets are fixed.

    Returns ``(new_text, omitted_bytes, deduped)``; ``(text, 0, False)`` when
    there is nothing to elide. Cuts prefer a line boundary and are moved to the
    nearest UTF-8 boundary, so the visible head is exactly the bytes before
    ``start`` and the visible tail is exactly the bytes from ``end`` on.
    """
    raw = text.encode("utf-8", "replace")
    head_bytes = max(0, head_bytes)
    tail_bytes = max(0, tail_bytes)
    if head_bytes + tail_bytes <= 0 or len(raw) <= head_bytes + tail_bytes:
        return text, 0, False
    head_raw = raw[:_utf8_head_end(raw, head_bytes)]
    tail_start = _utf8_tail_start(raw, len(raw) - tail_bytes) if tail_bytes else len(raw)
    tail_raw = raw[tail_start:]
    cut = head_raw.rfind(b"\n")
    if cut >= len(head_raw) // 2:
        head_raw = head_raw[:cut + 1]
    if tail_raw:
        first_newline = tail_raw.find(b"\n")
        if 0 < first_newline <= len(tail_raw) // 2:
            tail_raw = tail_raw[first_newline + 1:]
    omitted = len(raw) - len(head_raw) - len(tail_raw)
    if omitted <= 0:
        return text, 0, False
    start = len(head_raw)
    end = len(raw) - len(tail_raw)
    # Both sides are character-aligned, so this decode drops nothing. It is
    # strict on purpose: silently ignoring bytes is the bug being prevented.
    head = head_raw.decode("utf-8")
    tail = tail_raw.decode("utf-8")
    clean_head_text = strip_ansi(head)
    clean_head = collapse_repeats(clean_head_text, ref=ref, line_base=0)
    # A tail fragment can start inside a line, or between the two bytes of a
    # CRLF. Line numbers inside it are offset by the lines already complete in
    # the original; a fragment that starts with a break would add a line that
    # restore does not count, so it still collapses but quotes no line hint.
    clean_tail_text = strip_ansi(tail)
    tail_ok = bool(clean_tail_text) and clean_tail_text[0] not in _LINE_BREAKS
    clean_tail = collapse_repeats(clean_tail_text, ref=ref,
                                  line_base=_line_base(raw, end),
                                  line_numbers=tail_ok)
    deduped = clean_head != head or clean_tail != tail
    nl = "\r\n" if head_raw.endswith(b"\r\n") else "\n"
    line = _marker_line(omitted, reason, ref, total=len(raw), start=start, end=end)
    sep_before = "" if not clean_head or clean_head.endswith("\n") else nl
    sep_after = "" if not clean_tail or clean_tail.startswith("\n") else nl
    return f"{clean_head}{sep_before}{line}{sep_after}{clean_tail}", omitted, deduped


# --- JSON documents --------------------------------------------------------

def _field_path(path: str, key: Any, index: int | None = None) -> str | None:
    """The JSON pointer for one string field, e.g. ``/items/3/log``.

    Returns None when that pointer cannot be quoted faithfully in a marker:
    it would exceed the pointer budget, or a key contains a character that
    would change what the marker says. Callers leave such a field alone rather
    than emit a pointer recovery cannot resolve.
    """
    segment = str(index) if index is not None else str(key)
    segment = segment.replace("~", "~0").replace("/", "~1")
    new = f"{path}/{segment}"
    if len(new.encode("utf-8")) > _MAX_FIELD_PATH:
        return None
    if any(char in _UNQUOTABLE_FIELD_CHARS for char in new):
        return None
    return new


def _count_json_strings(value: Any, depth: int = 0) -> int:
    """How many string fields are big enough to be worth compressing.

    Used to share one document budget between the fields, so a document with
    five large fields cannot spend the whole allowance on each of them.
    """
    if depth > _JSON_MAX_DEPTH:
        return 0
    if isinstance(value, str):
        return 1 if len(value) > 256 else 0
    if isinstance(value, list):
        return sum(_count_json_strings(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return sum(_count_json_strings(item, depth + 1) for item in value.values())
    return 0


def _compress_json_values(value: Any, depth: int = 0, path: str = "",
                          ref: Ref | None = None,
                          max_bytes: int = _DEFAULT_MAX_BYTES,
                          field_budget: int | None = None,
                          marker_reserve: int = _MARKER_BUDGET) -> tuple[Any, int, int, bool]:
    """Compress the string fields of a decoded document, preserving its shape.

    Returns ``(new_value, original_bytes, compressed_bytes, changed)``. Only
    strings are rewritten; every other type comes back untouched. Strings that
    cannot exceed the threshold are skipped without a second classification
    pass, which keeps a 10,000-field document cheap to inspect.

    ``field_budget`` caps each individual field. It is the document's
    ``max_bytes`` share for one field, not the whole budget: compressing every
    field to the full limit would blow the limit on a multi-field document.
    """
    budget = max_bytes if field_budget is None else max(64, int(field_budget))
    if depth > _JSON_MAX_DEPTH:
        return value, 0, 0, False
    if isinstance(value, str):
        # Four bytes per character is the worst case, so this bound is safe.
        if len(value) <= max_bytes // 4:
            return value, 0, 0, False
        if ref and not path:  # the document root itself: nothing to scope to
            return value, 0, 0, False
        field_ref = Ref(ref.id, ref.ttl_hours, field=path) if ref else None
        out, stats = compress(value, max_bytes=budget, head_bytes=budget // 2,
                              tail_bytes=budget // 4, ref=field_ref,
                              marker_reserve=marker_reserve)
        if stats["changed"]:
            return out, stats["original_bytes"], stats["compressed_bytes"], True
        return value, 0, 0, False
    if isinstance(value, list):
        items: list[Any] = []
        original = compressed = 0
        changed = False
        for index, item in enumerate(value):
            # Paths are computed on both passes (ref present or not), so the
            # fields skipped as unrepresentable are the same fields both times.
            child = _field_path(path, None, index)
            if child is None:  # pointer not representable: leave this field alone
                items.append(item)
                continue
            new, orig, comp, did = _compress_json_values(
                item, depth + 1, child, ref, max_bytes, field_budget, marker_reserve)
            items.append(new)
            original += orig
            compressed += comp
            changed = changed or did
        return (items if changed else value), original, compressed, changed
    if isinstance(value, dict):
        out_map: dict[Any, Any] = {}
        original = compressed = 0
        changed = False
        for key, item in value.items():
            child = _field_path(path, key)
            if child is None:  # pointer not representable: leave this field alone
                out_map[key] = item
                continue
            new, orig, comp, did = _compress_json_values(
                item, depth + 1, child, ref, max_bytes, field_budget, marker_reserve)
            out_map[key] = new
            original += orig
            compressed += comp
            changed = changed or did
        return (out_map if changed else value), original, compressed, changed
    return value, 0, 0, False


# --- policies --------------------------------------------------------------

def _compress_impl(text: str, *, max_bytes: int | None = None, head_bytes: int | None = None,
                   tail_bytes: int | None = None, json_items: int | None = None,
                   ref: Ref | None = None, marker_reserve: int | None = None,
                   cfg: config.Config | None = None) -> tuple[str, dict[str, Any]]:
    """Compress one tool result. Returns ``(possibly new text, stats)``."""
    cfg = cfg or config.resolve()
    if max_bytes is None:
        max_bytes = cfg.max_bytes
    if head_bytes is None:
        head_bytes = cfg.head_bytes
    if tail_bytes is None:
        tail_bytes = cfg.tail_bytes
    max_bytes = max(1, int(max_bytes))
    head_bytes = max(0, int(head_bytes))
    tail_bytes = max(0, int(tail_bytes))
    reserve = _MARKER_BUDGET if marker_reserve is None else max(0, int(marker_reserve))
    if json_items or cfg.json_items:
        _warn_json_items_retired()

    original_bytes = len(text.encode("utf-8", "replace"))
    stats: dict[str, Any] = {
        "original_bytes": original_bytes,
        "compressed_bytes": original_bytes,
        "changed": False,
        "rule": "none",
        "omitted_bytes": 0,
        "partial": False,
    }
    if original_bytes <= max_bytes:
        return text, stats
    if original_bytes > _MAX_INPUT_BYTES:
        stats["rule"] = "too-large-kept"
        return text, stats

    work = strip_ansi(text)
    cls = _classify_work(work)

    if cls.kind in {KIND_EMPTY, KIND_PATCH}:
        stats["rule"] = "diff-kept" if cls.kind == KIND_PATCH else "empty-kept"
        return text, stats

    if cls.kind == KIND_JSON:
        # One document, one budget: split it across the fields that qualify, and
        # let each field reserve only its own share for the marker. If escaping
        # or marker text pushes the document over the cap, shrink the shares and
        # try again rather than handing back an oversized document.
        fields = max(1, _count_json_strings(cls.document))
        skeleton = min(64, max_bytes // 8)
        share = max(256, (max_bytes - skeleton) // fields)
        for _attempt in range(4):
            field_reserve = min(_MARKER_BUDGET, max(64, share // 2))
            new_doc, _, _, changed = _compress_json_values(
                cls.document, ref=ref, max_bytes=max_bytes, field_budget=share,
                marker_reserve=field_reserve)
            # A document in must be a document out, of the same top-level type.
            if not changed or type(new_doc) is not type(cls.document):
                break
            encoded = json.dumps(new_doc, ensure_ascii=False)
            encoded_bytes = len(encoded.encode("utf-8"))
            if not _parses_as_json(encoded) or encoded_bytes >= original_bytes:
                break
            if encoded_bytes <= max_bytes:
                stats.update(compressed_bytes=encoded_bytes, changed=True,
                             rule="json-fields",
                             omitted_bytes=max(0, original_bytes - encoded_bytes),
                             partial=True)
                return encoded, stats
            if share <= 256:
                break
            share = max(256, int(share * 0.75))
        stats["rule"] = "json-kept"
        return text, stats

    if cls.kind == KIND_TRACEBACK:
        tb_head, tb_tail = _traceback_limits(max_bytes, head_bytes, tail_bytes, reserve)
        if tb_head + tb_tail <= 0:
            stats["rule"] = "budget-too-small"
            return text, stats
        out, omitted, deduped = _elide_middle(
            text, tb_head, tb_tail, "traceback: error end kept", ref)
        if out == text:
            return text, stats
        stats.update(compressed_bytes=len(out.encode("utf-8")), changed=True,
                     rule="traceback" if omitted else "ansi", omitted_bytes=omitted,
                     partial=bool(omitted) or deduped)
        return out, stats

    head, tail = _limits(max_bytes, head_bytes, tail_bytes, reserve)
    if head + tail <= 0:
        stats["rule"] = "budget-too-small"
        return text, stats
    out, omitted, deduped = _elide_middle(text, head, tail, "large output", ref)
    if out == text:
        return text, stats
    if omitted and deduped:
        rule = "head-tail+dedupe"
    elif omitted:
        rule = "head-tail"
    elif deduped:
        rule = "dedupe"
    else:
        rule = "ansi"
    stats.update(compressed_bytes=len(out.encode("utf-8")), changed=True,
                 rule=rule, omitted_bytes=omitted, partial=bool(omitted) or deduped)
    return out, stats


def _warn_json_items_retired() -> None:
    global _json_items_warned
    if _json_items_warned:
        return
    _json_items_warned = True
    logger.warning(
        "sutra: SUTRA_JSON_ITEMS is retired and ignored. Eliding items from a JSON "
        "array changed the payload's top-level type (array in, object out); document "
        "string fields are still compressed and the original is kept for recovery.")


def compress(text: str, *, max_bytes: int | None = None, head_bytes: int | None = None,
             tail_bytes: int | None = None, json_items: int | None = None,
             ref: Ref | None = None, marker_reserve: int | None = None,
             cfg: config.Config | None = None) -> tuple[str, dict[str, Any]]:
    """Compress one result, with the format invariant enforced last.

    If the input was a JSON document and the output no longer parses, the
    original text is returned untouched. A broken payload is never worth the
    bytes saved.
    """
    original_bytes = len(text.encode("utf-8", "replace"))
    try:
        out, stats = _compress_impl(text, cfg=cfg, max_bytes=max_bytes, head_bytes=head_bytes,
                                    tail_bytes=tail_bytes, json_items=json_items, ref=ref,
                                    marker_reserve=marker_reserve)
    except Exception:  # fail open: never let a compressor break a tool result
        logger.exception("sutra: compression failed; returning the original")
        return text, {"original_bytes": original_bytes, "compressed_bytes": original_bytes,
                      "changed": False, "rule": "fail-open", "omitted_bytes": 0,
                      "fail_open": True}
    if out != text and not _parses_as_json(out) and _parse_json_document(text) is not None:
        return text, {
            "original_bytes": original_bytes,
            "compressed_bytes": original_bytes,
            "changed": False,
            "rule": "json-kept",
            "omitted_bytes": 0,
        }
    return out, stats
