"""Trim an outbound model request where the bytes are already recoverable.

The ``tool_execution`` middleware shrinks a result once, on the way in. Every
later turn re-sends that result, and on a long agent session the oldest ones are
mostly dead weight. This module is the other half: it runs on the
``llm_request`` frame, which Hermes calls with the assembled provider request,
and it reduces *older* tool results in the outbound copy only. The stored
conversation is never touched.

The rule that makes it safe is narrow on purpose: **a result is trimmed only
when its bytes are recoverable.** Two shapes qualify, and nothing else does:

1. *Already recoverable.* The content carries a SUTRA recovery marker with an
   id in it — the marker is written exactly when an original was retained in
   the store.
2. *Made recoverable here.* A host-produced tool result with no marker is
   retained in the store first, in the same call that shortens it, and the
   shortened copy quotes the new id. If the store declines the write (disabled,
   quota, oversized entry, I/O error) the message is left byte-identical,
   because trimming it would destroy the only copy the session has.

The two paths differ only in where the original comes from; the safety
property is the same one: **no byte leaves the request unless an id can bring it
back.** Host retention is what turns ``llm_request`` from "re-trim what SUTRA
already compressed" into real access to the whole conversation — the frame
carries every message the host assembled, including results SUTRA never saw.

On top of that rule the policy is deliberately conservative:

- only tool results are candidates; system, user and assistant text is never
  rewritten, so the instructions and the model's own words are never altered.
  A conversation may arrive as ``messages`` — where a result is either a
  ``role: tool`` message (chat) or a ``tool_result`` block inside a user
  message (Anthropic) — or as ``input`` (Responses and Codex shapes, where a
  result is a ``function_call_output`` item). All three are handled, and a
  top-level system prompt (``system``, ``instructions``) is never read or
  modified;
- the most recent ``keep_last`` messages are never touched, so the turn the
  model is answering is intact;
- a request smaller than ``min_request_bytes`` is left alone, because trimming
  three kilobytes from a short conversation is not worth any risk;
- a message smaller than ``min_message_bytes`` is left alone;
- at most ``max_messages`` results are shortened or collapsed per request, so
  one request never changes more than a bounded number of messages;
- every trimmed message keeps its marker, its first ``head_bytes`` and its last
  ``tail_bytes``, plus one line saying what happened. Content that would end up
  larger than the original is not rewritten;
- which older results go first is decided by the context formula
  ``V = w_r R + w_i I + w_d D + w_u U + w_t T`` (``context.values``), not by
  age alone: the lowest-value result is trimmed first, and a result that a later
  message cites by its recovery id is marked mandatory and never trimmed;
- any unexpected shape (a non-dict request, a non-list ``messages``, a content
  type this module does not model, a marker that cannot be located) returns the
  request unchanged.

The whole thing is off unless ``SUTRA_PROMPT=1`` and the mode is not ``off``.
"""

from __future__ import annotations

import copy
import hashlib
import re
import threading
from dataclasses import dataclass, field
from typing import Any

DEFAULT_KEEP_LAST = 6
DEFAULT_MIN_MESSAGE_BYTES = 8_192
DEFAULT_MIN_REQUEST_BYTES = 65_536
DEFAULT_MAX_MESSAGES = 8
DEFAULT_STORE_HOST_RESULTS = True
HEAD_BYTES = 1_536
TAIL_BYTES = 768
MAX_MARKER_BYTES = 1_024
MAX_RETAINED = 256

# The notice written at the top of every shortened copy. Its presence is also
# how the module recognises a copy it already shortened, so a second pass over
# the same request cannot shrink it again.
NOTICE_PREFIX = "[sutra: request copy ·"

# A marker is the bracketed line the engine appends when it retains an original.
_MARKER = re.compile(r"\[sutra:[^\]\n]{0,600}\]")
_ID = re.compile(r"\bid=[\"']?([A-Za-z0-9_-]{4,})")

# Process-wide memo: (store directory, sha256 of the payload) -> entry id. It
# is keyed by directory so a test or an operator who points SUTRA at another
# store never reuses an id that belongs to the old one. A host may call the
# request frame from more than one session at a time, so the table is guarded:
# the store write happens outside the lock, and only the short lookups and the
# bounded eviction take it.
_RETAINED: dict[tuple[str, str], str] = {}
_RETAINED_LOCK = threading.Lock()


@dataclass
class Decision:
    """What the request middleware decided, and why."""

    request: dict[str, Any] | None = None
    elided: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    reason: str = ""
    messages: list[int] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.request is not None

    @property
    def saved_bytes(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)


def _size(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


def _texts(content: Any) -> list[str]:
    """Every text fragment of a message payload, whatever container it uses."""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "tool_result":
            texts.extend(_texts(block.get("content")))
    return texts


def _has_marker(content: Any) -> bool:
    """True when any fragment of this payload can be restored by id."""
    return any(_markers(text) for text in _texts(content))


def _markers(text: str) -> list[str]:
    """The recoverable markers inside a payload, bounded and id-bearing."""
    if not isinstance(text, str) or "sutra:" not in text:
        return []
    found = []
    total = 0
    for match in _MARKER.finditer(text):
        marker = match.group(0)
        if not _ID.search(marker):
            continue
        if total + len(marker) > MAX_MARKER_BYTES:
            break
        found.append(marker)
        total += len(marker)
    return found


def _host_marker(entry_id: str) -> str:
    """The marker that makes a host-produced original recoverable by id."""
    return (
        f'[sutra: elided for this request · original retained · id="{entry_id}" · '
        f'call sutra_restore with this id to read it]'
    )


def _duplicate_marker(entry_id: str, size: int) -> str:
    """The pointer left where the same result appeared again in one request."""
    return (
        f'[sutra: repeated result · {size:,} bytes identical to an earlier '
        f'result in this request · id="{entry_id}" · call sutra_restore with '
        f'this id to read it]'
    )


def host_retainer(*, session_id: str | None = None, store: Any = None):
    """Return ``retain(text) -> marker | None`` backed by the local store.

    The store is resolved on first use, so a request with nothing to shorten
    pays nothing. Every failure — store disabled, quota exceeded, entry too
    large, I/O error — returns ``None``, and the caller then leaves that message
    byte-identical. Retention is never allowed to cost the request.
    """
    memo: dict[str, str] = {}

    def retain(text: str) -> str | None:
        try:
            handle = store
            if handle is None:
                from .store import store_from_env

                handle = store_from_env()
            directory = str(getattr(handle, "dir", ""))
            digest = hashlib.sha256(
                text.encode("utf-8", "surrogatepass")).hexdigest()
            key = (directory, digest)
            # Single flight per payload: two sessions retaining the same body
            # at the same moment must agree on one id, or the two conversations
            # would carry markers that name different copies of the same bytes.
            with _RETAINED_LOCK:
                cached = memo.get(digest) or _RETAINED.get(key)
                if cached and getattr(handle.get(cached), "status", "") == "ok":
                    memo[digest] = cached
                    return _host_marker(cached)
                _RETAINED.pop(key, None)
                entry = handle.put(
                    text, tool_name="request", session_id=session_id or "")
                if entry is None:
                    return None
                memo[digest] = entry.id
                _RETAINED[key] = entry.id
                while len(_RETAINED) > MAX_RETAINED:
                    _RETAINED.pop(next(iter(_RETAINED)))
                return _host_marker(entry.id)
        except Exception:  # fail open: no store, no retention, no trim
            return None

    return retain


def _shorten(
    text: str,
    *,
    head: int,
    tail: int,
    retain: Any = None,
) -> str | None:
    """A recoverable shortened copy of ``text``, or ``None`` when it is not one."""
    if not text or NOTICE_PREFIX in text:
        return None
    original = _size(text)
    if original <= head + tail + 128:
        return None
    markers = _markers(text)
    if not markers:
        if retain is None:
            return None
        marker = retain(text)
        if not marker:
            return None
        markers = [marker]
    if original <= head + tail + sum(_size(m) for m in markers) + 128:
        return None
    head_part = text[:head]
    tail_part = text[-tail:] if tail else ""
    kept = sum(_size(part) for part in (head_part, *markers, tail_part))
    trimmed = max(0, original - kept)
    notice = (
        f"{NOTICE_PREFIX} {trimmed:,} bytes trimmed from this older tool "
        f"result; the marker below carries the id that restores the original]"
    )
    parts = [notice, head_part, *markers]
    if tail_part:
        parts.append(tail_part)
    candidate = "\n".join(parts)
    if _size(candidate) >= original:
        return None
    return candidate


def shorten_result(
    text: str,
    *,
    head: int = HEAD_BYTES,
    tail: int = TAIL_BYTES,
    retain: Any = None,
) -> str | None:
    """Shorten one tool result for a host that hands results over one by one.

    Some hosts expose a request hook that carries the assembled conversation
    (OpenCode's ``chat.messages.transform`` does), but let a plugin rewrite a
    result only where that result already sits. This is the same policy as
    :func:`prepare` for one body: the text changes only when a marker can
    restore it, and ``None`` means "leave the text exactly as it is".
    """
    return _shorten(text, head=head, tail=tail, retain=retain)


def _content_size(content: Any) -> int:
    if isinstance(content, str):
        return _size(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                total += _size(block["text"])
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                total += _content_size(block.get("content"))
            elif isinstance(block, str):
                total += _size(block)
        return total
    return 0


MESSAGES_KEY = "messages"
INPUT_KEY = "input"


def _conversation(request: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """The conversation list this policy may touch, and the key it lives under.

    Chat and Anthropic requests carry ``messages``; Responses and Codex requests
    carry ``input`` items. The first usable list wins, and a request carrying
    neither is left alone.
    """
    for key in (MESSAGES_KEY, INPUT_KEY):
        items = request.get(key)
        if isinstance(items, list) and items:
            return key, items
    return None


def _tool_result_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The Anthropic ``tool_result`` blocks of one message, if it has any.

    The Messages API delivers a tool result inside a user message's content
    list, unlike the chat shape's dedicated ``role: tool`` message, so a policy
    that only looked at roles would leave every Claude Code request untouched.
    """
    content = item.get("content")
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


def _is_result(shape: str, item: Any) -> bool:
    """Whether one request item is a tool result, in this request shape."""
    if not isinstance(item, dict):
        return False
    if shape == MESSAGES_KEY:
        if item.get("role") == "tool":
            return True
        return item.get("role") == "user" and bool(_tool_result_blocks(item))
    return item.get("type") == "function_call_output"


def _payload(shape: str, item: dict[str, Any]) -> Any:
    """The value that holds the text of one item."""
    return item.get("content") if shape == MESSAGES_KEY else item.get("output")


def _with_payload(shape: str, item: dict[str, Any], value: Any) -> dict[str, Any]:
    field = "content" if shape == MESSAGES_KEY else "output"
    return {**item, field: value}


def _spoken(shape: str, item: Any) -> str:
    """Text one item adds to the conversation, never a result payload."""
    if not isinstance(item, dict) or _is_result(shape, item):
        return ""
    if shape == MESSAGES_KEY:
        return "\n".join(_texts(item.get("content")))
    if item.get("type") == "message":
        return "\n".join(_texts(item.get("content")))
    return ""


def _last_user_text(shape: str, items: list[Any]) -> str:
    """What the model was most recently asked, in either shape."""
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        if shape == MESSAGES_KEY:
            if item.get("role") == "user":
                return "\n".join(_texts(item.get("content")))
            continue
        if item.get("type") == "message" and item.get("role") == "user":
            return "\n".join(_texts(item.get("content")))
    return ""


def _rewrite_tool_result(
    block: dict[str, Any], *, head: int, tail: int, retain: Any = None
) -> tuple[dict[str, Any] | None, int]:
    """Shorten the content of one ``tool_result`` block, keeping every other key.

    ``tool_use_id``, ``is_error`` and ``cache_control`` describe the block, not
    its text, so they travel unchanged; only the payload the block carries is
    replaced.
    """
    content = block.get("content")
    if isinstance(content, str):
        trimmed = _shorten(content, head=head, tail=tail, retain=retain)
        if trimmed is None:
            return None, 0
        return {**block, "content": trimmed}, _size(content) - _size(trimmed)
    if not isinstance(content, list):
        return None, 0
    saved = 0
    parts: list[Any] = []
    changed = False
    for part in content:
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            parts.append(part)
            continue
        trimmed = _shorten(part["text"], head=head, tail=tail, retain=retain)
        if trimmed is None:
            parts.append(part)
            continue
        saved += _size(part["text"]) - _size(trimmed)
        parts.append({**part, "text": trimmed})
        changed = True
    if not changed:
        return None, 0
    return {**block, "content": parts}, saved


def _rewrite_content(
    content: Any, *, head: int, tail: int, retain: Any = None
) -> tuple[Any, int] | None:
    """Rewrite one message's content if it is recoverable; else ``None``."""
    if isinstance(content, str):
        trimmed = _shorten(content, head=head, tail=tail, retain=retain)
        if trimmed is None:
            return None
        return trimmed, _size(content) - _size(trimmed)
    if not isinstance(content, list):
        return None
    saved = 0
    blocks: list[Any] = []
    changed = False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            rewritten, block_saved = _rewrite_tool_result(
                block, head=head, tail=tail, retain=retain
            )
            if rewritten is None:
                blocks.append(block)
                continue
            blocks.append(rewritten)
            saved += block_saved
            changed = True
            continue
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            blocks.append(block)
            continue
        trimmed = _shorten(block["text"], head=head, tail=tail, retain=retain)
        if trimmed is None:
            blocks.append(block)
            continue
        saved += _size(block["text"]) - _size(trimmed)
        blocks.append({**block, "text": trimmed})
        changed = True
    if not changed:
        return None
    return blocks, saved


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{3,}")


def _identifiers(text: str) -> set[str]:
    """The name-like tokens of a payload, used only as an overlap signal."""
    return set(_WORD.findall(text))


def _distinctive(token: str) -> bool:
    """True for a token that names something, not for an ordinary English word.

    A path separator, an underscore, a dot, a digit or an inner capital all mark
    a name a later message would have to quote to refer to this payload; ``error``
    or ``output`` carry none of them and are therefore not dependencies.
    """
    if "_" in token or "/" in token or "." in token:
        return True
    if any(char.isdigit() for char in token):
        return True
    return any(char.isupper() for char in token[1:])


def _later_tokens(shape: str, items: list[Any]) -> list[set[str]]:
    """For every position, the name-like tokens the messages after it quote.

    Only assistant and user text counts, for the same reason a marker inside
    another tool result is not a citation: a tool result that happens to print a
    path — a ``grep`` hit, a test listing — mentions the name without depending
    on the payload that produced it, and treating it as a dependency would make
    almost every result in a real session mandatory.

    The suffix is built once per request. Testing a candidate against it is a
    membership test plus, for dependency protection, a substring test over the
    tokens — never a fresh scan of a possibly very large result body. That is
    what keeps the policy linear in request size instead of linear in
    ``results × body_size``.
    """
    suffix: list[set[str]] = [set() for _ in range(len(items) + 1)]
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        spoken = "" if _is_result(shape, item) else _spoken(shape, item)
        suffix[index] = suffix[index + 1] | _identifiers(spoken)
    return suffix


def _recovery_ids(text: str) -> set[str]:
    ids = set()
    for marker in _markers(text):
        match = _ID.search(marker)
        if match:
            ids.add(match.group(1))
    return ids


def _candidates(
    shape: str,
    items: list[Any],
    *,
    first_candidate: int,
    min_message_bytes: int,
    retain: Any = None,
) -> tuple[list[tuple[float, int]], dict[int, float]]:
    """Rank the trimmable older tool results by context value, lowest first.

    The score is ``context.values`` over components this module can actually
    observe in a request: relevance is "a later assistant or user message cites
    this result's recovery id", importance is the identifier overlap with the
    most recent user message, usefulness is "this payload is restorable",
    recency is the item position. A cited result is mandatory and never
    trimmed, and so is a result a later message depends on: one that contains a
    distinctive name — a file path, a symbol, an id-like token — quoted after
    it, because shortening it would remove the text that message refers to. A
    marker inside another tool result never counts as a citation, because every
    compressed result carries its own.

    An unmarked result is a candidate only when ``retain`` is supplied — that is
    the only case in which the original can be kept before the copy is cut.
    """
    asked = _identifiers(_last_user_text(shape, items))
    later_tokens = _later_tokens(shape, items)

    prepared: list[tuple[int, str, bool]] = []
    for index in range(first_candidate):
        item = items[index]
        if not _is_result(shape, item):
            continue
        text = "\n".join(_texts(_payload(shape, item)))
        if _size(text) < max(0, min_message_bytes) or NOTICE_PREFIX in text:
            continue
        ids = _recovery_ids(text)
        if not ids and retain is None:
            continue
        after = later_tokens[index + 1]
        cited = bool(ids & after)
        depended_on = any(
            _distinctive(token) and token in text for token in after)
        prepared.append((index, text, cited or depended_on))

    if not prepared:
        return [], {}

    try:
        from . import context as _context
    except Exception:  # a packaging problem must not change what gets sent
        return sorted(((0.0, index) for index, _, _ in prepared)), {}

    total = max(1, len(items) - 1)
    scored = [
        _context.Item(
            id=f"m{index}",
            content=text,
            recency=min(1.0, index / total),
            relevance=1.0 if cited else 0.0,
            importance=(
                min(1.0, sum(1 for token in asked if token in text) / len(asked))
                if asked else 0.0
            ),
            usefulness=1.0,
            mandatory=cited,
            source="request",
        )
        for index, text, cited in prepared
    ]
    scores = _context.values(scored)
    ordered = [
        (scores[item.id], int(item.id[1:]))
        for item in scored
        if not item.mandatory
    ]
    ordered.sort(key=lambda pair: (pair[0], pair[1]))
    return ordered, scores


def _duplicates(
    shape: str,
    items: list[Any],
    *,
    first_candidate: int,
    min_message_bytes: int,
) -> dict[int, int]:
    """Map each repeated payload in the older window onto its first occurrence.

    ``first_candidate`` is the exclusive end of the window the policy may touch,
    matching ``_candidates``.
    """
    first: dict[bytes, int] = {}
    duplicates: dict[int, int] = {}
    for index in range(max(0, first_candidate)):
        item = items[index]
        if not _is_result(shape, item):
            continue
        payload = _payload(shape, item)
        if _content_size(payload) < max(0, min_message_bytes):
            continue
        texts = _texts(payload)
        if not texts or any(NOTICE_PREFIX in text for text in texts):
            continue
        digest = hashlib.sha256(
            "\x00".join(texts).encode("utf-8", "surrogatepass")
        ).digest()
        if digest in first:
            duplicates[index] = first[digest]
        else:
            first[digest] = index
    return duplicates


def _pointer_content(payload: Any, pointer: str) -> Any:
    """The pointer in the payload's own container, or ``None`` if it has none.

    A tool result is a string in the chat shapes and can be a list of content
    blocks in the block shapes. Replacing a block list with a bare string would
    change the payload's type, which the request may reject, so the pointer goes
    into the first text block and the remaining text leaves with it; non-text
    blocks (an image, say) are kept where they were.
    """
    if isinstance(payload, str):
        return pointer
    if not isinstance(payload, list):
        return None
    results = [block for block in payload
               if isinstance(block, dict) and block.get("type") == "tool_result"]
    if results:
        # Every tool_use in the previous assistant turn needs its result, so a
        # message is collapsed only when it carries exactly one result and no
        # other content: replacing it with a pointer must not drop a reference.
        if len(results) != 1 or len(payload) != 1:
            return None
        return [{**results[0], "content": pointer}]
    blocks: list[Any] = []
    placed = False
    for block in payload:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            if placed:
                continue
            blocks.append({**block, "text": pointer})
            placed = True
        elif isinstance(block, str):
            if placed:
                continue
            blocks.append(pointer)
            placed = True
        else:
            blocks.append(block)
    return blocks if placed else None


def _collapse_duplicates(
    shape: str,
    items: list[Any],
    updated: list[Any],
    *,
    duplicates: dict[int, int],
    limit: int,
) -> tuple[list[int], int]:
    """Point later repeats of one payload at an id already in this request.

    A repeated payload is pure redundancy: the same bytes are already in the
    request once. When that first copy carries a recovery id — because it was
    already marked, or because this call shortened it — the repeat is replaced
    by a pointer to the same id. A repeat whose first copy has no id is left
    alone: inventing one would mean a store write nothing asked for, and so is a
    repeat whose payload is not a container this module can put a pointer into.
    """
    collapsed: list[int] = []
    saved = 0
    if max(0, limit) <= 0:
        return collapsed, saved
    for index, source in sorted(duplicates.items()):
        ids = sorted(
            id_
            for id_ in _recovery_ids(
                "\n".join(_texts(_payload(shape, updated[source])))
            )
        )
        if not ids:
            continue
        payload = _payload(shape, updated[index])
        size = _content_size(payload)
        pointer = _duplicate_marker(ids[0], size)
        replacement = _pointer_content(payload, pointer)
        if replacement is None:
            continue
        pointer_size = _content_size(replacement)
        if pointer_size >= size:
            continue
        updated[index] = _with_payload(shape, updated[index], replacement)
        collapsed.append(index)
        saved += size - pointer_size
        if len(collapsed) >= max(1, limit):
            break
    return collapsed, saved


def prepare(
    request: Any,
    *,
    keep_last: int = DEFAULT_KEEP_LAST,
    min_message_bytes: int = DEFAULT_MIN_MESSAGE_BYTES,
    min_request_bytes: int = DEFAULT_MIN_REQUEST_BYTES,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    head_bytes: int = HEAD_BYTES,
    tail_bytes: int = TAIL_BYTES,
    retain: Any = None,
) -> Decision:
    """Trim recoverable older tool results in one request; never raise."""
    reason = _refusal(request, keep_last=keep_last,
                      min_request_bytes=min_request_bytes, retain=retain)
    if reason:
        return Decision(reason=reason)

    shape, items = _conversation(request)
    total_before = sum(_content_size(_payload(shape, item)) for item in items)
    first_candidate = max(0, len(items) - max(0, keep_last))

    duplicates = _duplicates(
        shape,
        items,
        first_candidate=first_candidate,
        min_message_bytes=min_message_bytes,
    )

    order, scores = _candidates(
        shape,
        items,
        first_candidate=first_candidate,
        min_message_bytes=min_message_bytes,
        retain=retain,
    )

    updated = copy.deepcopy(items)
    elided_at: list[int] = []
    trimmed_scores: list[float] = []
    saved_total = 0
    for value, index in order:
        if index in duplicates:
            continue
        item = updated[index]
        if not _is_result(shape, item):
            continue
        payload = _payload(shape, item)
        if _content_size(payload) < max(0, min_message_bytes):
            continue
        outcome = _rewrite_content(
            payload, head=max(0, head_bytes),
            tail=max(0, tail_bytes), retain=retain,
        )
        if outcome is None:
            continue
        updated[index] = _with_payload(shape, item, outcome[0])
        saved_total += outcome[1]
        elided_at.append(index)
        trimmed_scores.append(value)
        if len(elided_at) >= max(1, max_messages):
            break

    collapsed, collapsed_saved = _collapse_duplicates(
        shape,
        items,
        updated,
        duplicates=duplicates,
        limit=max(0, max_messages) - len(elided_at),
    )
    saved_total += collapsed_saved
    touched = sorted(elided_at + collapsed)

    if not touched:
        if scores and not order:
            return Decision(reason="every recoverable result is cited by a later message")
        if retain is not None:
            return Decision(reason="no older tool result could be retained and shortened")
        return Decision(reason="no older tool result carried a recoverable marker")

    new_request = dict(request)
    new_request[shape] = updated
    parts = []
    if elided_at:
        values = ", ".join(f"{value:.2f}" for value in trimmed_scores)
        parts.append(
            f"trimmed {len(elided_at)} older tool result(s) with the lowest "
            f"context value ({values})"
        )
    if collapsed:
        parts.append(
            f"collapsed {len(collapsed)} repeated result(s) onto an id already "
            f"in this request"
        )
    return Decision(
        request=new_request,
        elided=len(touched),
        bytes_before=total_before,
        bytes_after=max(0, total_before - saved_total),
        reason="; ".join(parts) + "; originals still in the session and in the store",
        messages=touched,
    )


def _refusal(request: Any, *, keep_last: int, min_request_bytes: int,
             retain: Any = None) -> str:
    if not isinstance(request, dict):
        return "the request is not a mapping"
    conversation = _conversation(request)
    if conversation is None:
        return "the request has no message list this adapter models"
    shape, items = conversation
    if any(not isinstance(item, dict) for item in items):
        return "a message is not a mapping"
    if len(items) <= max(0, keep_last):
        return "the request is short enough that every message is recent"
    total = sum(_content_size(_payload(shape, item)) for item in items)
    if total < max(0, min_request_bytes):
        return f"the request carries {total:,} bytes, under the floor"
    if retain is None and not any(
        _is_result(shape, item) and _has_marker(_payload(shape, item))
        for item in items
    ):
        return "no tool result in this request carries a recoverable marker"
    return ""


def summarise(request: Any, **options: Any) -> dict[str, Any]:
    """A JSON-safe description of what ``prepare`` would do, for a dry run."""
    decision = prepare(request, **options)
    return {
        "changed": decision.changed,
        "elided": decision.elided,
        "bytes_before": decision.bytes_before,
        "bytes_after": decision.bytes_after,
        "saved_bytes": decision.saved_bytes,
        "messages": list(decision.messages),
        "reason": decision.reason,
    }
