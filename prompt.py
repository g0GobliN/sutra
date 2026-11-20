"""Trim an outbound model request where the bytes are already recoverable.

The ``tool_execution`` middleware shrinks a result once, on the way in. Every
later turn re-sends that result, and on a long agent session the oldest ones are
mostly dead weight. This module is the other half: it runs on the
``llm_request`` frame, which Hermes calls with the assembled provider request,
and it reduces *older* tool results in the outbound copy only. The stored
conversation is never touched.

The rule that makes it safe is narrow on purpose: **a result is trimmed only
when its bytes are already recoverable.** That means the content must carry a
SUTRA recovery marker with an id in it — the marker is written exactly when an
original was retained in the store. A tool result that SUTRA never compressed
carries no marker, and is left byte-identical, because trimming it would destroy
the only copy the session has.

On top of that rule the policy is deliberately conservative:

- only ``role: "tool"`` messages are candidates; system, user and assistant
  text is never rewritten;
- the most recent ``keep_last`` messages are never touched, so the turn the
  model is answering is intact;
- a request smaller than ``min_request_bytes`` is left alone, because trimming
  three kilobytes from a short conversation is not worth any risk;
- a message smaller than ``min_message_bytes`` is left alone;
- at most ``max_messages`` results are trimmed per request;
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
import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_KEEP_LAST = 6
DEFAULT_MIN_MESSAGE_BYTES = 8_192
DEFAULT_MIN_REQUEST_BYTES = 65_536
DEFAULT_MAX_MESSAGES = 8
HEAD_BYTES = 1_536
TAIL_BYTES = 768
MAX_MARKER_BYTES = 1_024

# A marker is the bracketed line the engine appends when it retains an original.
_MARKER = re.compile(r"\[sutra:[^\]\n]{0,600}\]")
_ID = re.compile(r"\bid=[\"']?([A-Za-z0-9_-]{4,})")


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
    return [block["text"] for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)]


def _has_marker(content: Any) -> bool:
    """True when any fragment of this payload can be restored by id."""
    return any(_markers(text) for text in _texts(content))


def _markers(text: str) -> list[str]:
    """The recoverable markers inside a payload, bounded and id-bearing."""
    if not isinstance(text, str):
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


def _trim(text: str, *, head: int, tail: int) -> str | None:
    """A recoverable shortened copy of ``text``, or ``None`` when it is not one."""
    markers = _markers(text)
    if not markers:
        return None
    original = _size(text)
    if original <= head + tail + sum(_size(m) for m in markers) + 128:
        return None
    head_part = text[:head]
    tail_part = text[-tail:] if tail else ""
    kept = sum(_size(part) for part in (head_part, *markers, tail_part))
    trimmed = max(0, original - kept)
    notice = (
        f"[sutra: request copy · {trimmed:,} bytes trimmed from this older tool "
        f"result; the original is still in the session and the id above still "
        f"restores it]"
    )
    parts = [notice, head_part, *markers]
    if tail_part:
        parts.append(tail_part)
    candidate = "\n".join(parts)
    if _size(candidate) >= original:
        return None
    return candidate


def _content_size(content: Any) -> int:
    if isinstance(content, str):
        return _size(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                total += _size(block["text"])
            elif isinstance(block, str):
                total += _size(block)
        return total
    return 0


def _rewrite_content(content: Any, *, head: int, tail: int) -> tuple[Any, int] | None:
    """Rewrite one message's content if it is recoverable; else ``None``."""
    if isinstance(content, str):
        trimmed = _trim(content, head=head, tail=tail)
        if trimmed is None:
            return None
        return trimmed, _size(content) - _size(trimmed)
    if not isinstance(content, list):
        return None
    saved = 0
    blocks: list[Any] = []
    changed = False
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            blocks.append(block)
            continue
        trimmed = _trim(block["text"], head=head, tail=tail)
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


def _recovery_ids(text: str) -> set[str]:
    ids = set()
    for marker in _markers(text):
        match = _ID.search(marker)
        if match:
            ids.add(match.group(1))
    return ids


def _candidates(
    messages: list[Any], *, first_candidate: int, min_message_bytes: int
) -> tuple[list[tuple[float, int]], dict[int, float]]:
    """Rank the trimmable older tool results by context value, lowest first.

    The score is ``context.values`` over components this module can actually
    observe in a request: relevance is "a later assistant or user message cites
    this result's recovery id", importance is the identifier overlap with the
    most recent user message, usefulness is "this payload is restorable",
    recency is the message position. A cited result is mandatory and never
    trimmed; a marker inside another tool result never counts as a citation,
    because every compressed result carries its own.
    """
    last_user = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            last_user = "\n".join(_texts(message.get("content")))
            break
    asked = _identifiers(last_user)

    prepared: list[tuple[int, str, bool]] = []
    for index in range(first_candidate):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        text = "\n".join(_texts(message.get("content")))
        if _size(text) < max(0, min_message_bytes):
            continue
        ids = _recovery_ids(text)
        if not ids:
            continue
        later = "\n".join(
            "\n".join(_texts(other.get("content")))
            for other in messages[index + 1:]
            if isinstance(other, dict) and other.get("role") != "tool"
        )
        cited = any(rid in later for rid in ids)
        prepared.append((index, text, cited))

    if not prepared:
        return [], {}

    try:
        from . import context as _context
    except Exception:  # a packaging problem must not change what gets sent
        return sorted(((0.0, index) for index, _, _ in prepared)), {}

    total = max(1, len(messages) - 1)
    items = [
        _context.Item(
            id=f"m{index}",
            content=text,
            recency=min(1.0, index / total),
            relevance=1.0 if cited else 0.0,
            importance=(
                min(1.0, len(_identifiers(text) & asked) / len(asked)) if asked else 0.0
            ),
            usefulness=1.0,
            mandatory=cited,
            source="request",
        )
        for index, text, cited in prepared
    ]
    scores = _context.values(items)
    ordered = [
        (scores[item.id], int(item.id[1:]))
        for item in items
        if not item.mandatory
    ]
    ordered.sort(key=lambda pair: (pair[0], pair[1]))
    return ordered, scores


def prepare(
    request: Any,
    *,
    keep_last: int = DEFAULT_KEEP_LAST,
    min_message_bytes: int = DEFAULT_MIN_MESSAGE_BYTES,
    min_request_bytes: int = DEFAULT_MIN_REQUEST_BYTES,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    head_bytes: int = HEAD_BYTES,
    tail_bytes: int = TAIL_BYTES,
) -> Decision:
    """Trim recoverable older tool results in one request; never raise."""
    reason = _refusal(request, keep_last=keep_last, min_request_bytes=min_request_bytes)
    if reason:
        return Decision(reason=reason)

    messages = request["messages"]
    total_before = sum(_content_size(m.get("content")) for m in messages)
    last_index = len(messages)
    first_candidate = max(0, min(last_index - max(0, keep_last), last_index))

    order, scores = _candidates(
        messages,
        first_candidate=first_candidate,
        min_message_bytes=min_message_bytes,
    )

    updated = copy.deepcopy(messages)
    elided_at: list[int] = []
    trimmed_scores: list[float] = []
    saved_total = 0
    for value, index in order:
        message = updated[index]
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        if _content_size(message.get("content")) < max(0, min_message_bytes):
            continue
        outcome = _rewrite_content(
            message.get("content"), head=max(0, head_bytes), tail=max(0, tail_bytes)
        )
        if outcome is None:
            continue
        message["content"], saved = outcome
        saved_total += saved
        elided_at.append(index)
        trimmed_scores.append(value)
        if len(elided_at) >= max(1, max_messages):
            break

    if not elided_at:
        if scores and not order:
            return Decision(reason="every recoverable result is cited by a later message")
        return Decision(reason="no older tool result carried a recoverable marker")

    new_request = dict(request)
    new_request["messages"] = updated
    values = ", ".join(f"{value:.2f}" for value in trimmed_scores)
    return Decision(
        request=new_request,
        elided=len(elided_at),
        bytes_before=total_before,
        bytes_after=max(0, total_before - saved_total),
        reason=(
            f"trimmed {len(elided_at)} older tool result(s) with the lowest "
            f"context value ({values}); originals still in the session and in "
            f"the store"
        ),
        messages=elided_at,
    )


def _refusal(request: Any, *, keep_last: int, min_request_bytes: int) -> str:
    if not isinstance(request, dict):
        return "the request is not a mapping"
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return "the request has no message list this adapter models"
    if any(not isinstance(message, dict) for message in messages):
        return "a message is not a mapping"
    if len(messages) <= max(0, keep_last):
        return "the request is short enough that every message is recent"
    total = sum(_content_size(message.get("content")) for message in messages)
    if total < max(0, min_request_bytes):
        return f"the request carries {total:,} bytes, under the floor"
    if not any(
        message.get("role") == "tool" and _has_marker(message.get("content"))
        for message in messages
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
