"""UTF-8 character-boundary arithmetic shared by compression and recovery.

A UTF-8 continuation byte carries ``10`` in its top two bits. In valid UTF-8,
a byte offset is a character boundary exactly when it is outside the data or
does not point at a continuation byte. This module is the only place that rule
is encoded: the engine moves its two elision cuts, and recovery validates a
requested range and moves a clipped line's start.
"""

from __future__ import annotations


def is_continuation(byte: int) -> bool:
    """True when ``byte`` continues a multi-byte UTF-8 character."""
    return (byte & 0xC0) == 0x80


def is_boundary(data: bytes, offset: int) -> bool:
    """True when ``offset`` is a valid place to split UTF-8 ``data``."""
    return offset <= 0 or offset >= len(data) or not is_continuation(data[offset])


def end_at_or_before(data: bytes, offset: int) -> int:
    """Move a cut back out of a multi-byte character.

    ``data`` must be valid UTF-8. An offset at or past the end is the end;
    otherwise only the character containing ``offset`` is excluded, so the
    prefix decodes exactly.
    """
    if offset >= len(data):
        return len(data)
    while offset > 0 and is_continuation(data[offset]):
        offset -= 1
    return max(0, offset)


def start_at_or_after(data: bytes, offset: int) -> int:
    """Move a cut forward out of a multi-byte character."""
    while offset < len(data) and is_continuation(data[offset]):
        offset += 1
    return offset
