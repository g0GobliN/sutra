"""Deterministic, opt-in wording compression for prose lines.

This is the conservative form of the "Caveman-inspired" idea: remove words
that carry no technical meaning, never rewrite code, and let the protected-span
validator decide whether the result is acceptable. It is deterministic — a
fixed table of verbose phrases and politeness fillers — and it refuses to touch
a line that looks like code, contains a quote or a template placeholder, or is
inside a fenced block.

Deliberately *not* implemented: paraphrasing, sentence rewriting, synonym
selection, dropping whole sentences. Those need a model and a semantic
guarantee this project does not have; claiming them from a word table would be
dishonest. Everything here is a strict substring replacement that keeps the
sentence's grammatical skeleton, and every candidate must still pass
``preserve.preservation_ok`` (negations, numbers, identifiers, commands,
paths, URLs, error and security terms) before the optimizer may apply it.
"""

from __future__ import annotations

import re
from typing import Iterable

# Longest phrase first: "in order to" must win over "in order".
_PHRASES: tuple[tuple[str, str], ...] = (
    ("due to the fact that", "because"),
    ("in spite of the fact that", "although"),
    ("for the purpose of", "to"),
    ("in the event that", "if"),
    ("at this point in time", "now"),
    ("with regard to", "about"),
    ("in order to", "to"),
    ("has the ability to", "can"),
    ("is able to", "can"),
    ("a large number of", "many"),
    ("make use of", "use"),
    ("make sure to", "ensure"),
    ("it is important to note that", ""),
    ("please note that", ""),
    ("as a matter of fact", ""),
    ("could you please", ""),
    ("i would like you to", ""),
    ("kindly", ""),
    ("please", ""),
    ("basically", ""),
    ("actually", ""),
)

# The wording pass is cheap per line but the protected-span check re-reads the
# whole payload, so it is skipped on very large inputs rather than spending a
# tool call's latency budget on a handful of words.
MAX_BYTES = 64 * 1024

_FENCE = ("```", "~~~")
_MARKER_PREFIX = "[sutra:"
# A line with any of these is not treated as prose: the replacement could land
# inside a literal, an interpolation or a piece of code the reader must see.
_CODE_HINTS = ('"', "'", "`", "${", "{{", "}}", "{", "}", ";", "=", "\t", "->", "::")

_COMPILED = tuple(
    (re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", re.IGNORECASE), replacement)
    for phrase, replacement in _PHRASES
)


def _squeeze(text: str) -> str:
    """Collapse the double spaces a removal can leave."""
    return re.sub(r" {2,}", " ", text).strip(" ")


def _keep_case(match: re.Match[str], replacement: str) -> str:
    original = match.group(0)
    if not replacement:
        return replacement
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def compress_line(line: str) -> str:
    """Rewrite one prose line; never raises, never returns a non-str."""
    stripped = line.strip()
    if not stripped or stripped.startswith(_MARKER_PREFIX):
        return line
    if any(hint in line for hint in _CODE_HINTS):
        return line
    indent = line[:len(line) - len(line.lstrip(" "))]
    out = line.strip(" ")
    capitalised_start = out[:1].isupper()
    for pattern, replacement in _COMPILED:
        out = pattern.sub(lambda m, r=replacement: _keep_case(m, r), out)
    out = _squeeze(out)
    if not out:
        return line
    if capitalised_start and out[:1].islower():
        out = out[:1].upper() + out[1:]
    return indent + out


def compress_text(text: str) -> str:
    """Rewrite prose lines outside fenced blocks and marker lines."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        return text
    lines: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        marker = line.strip()[:3]
        if fence is None and marker in _FENCE:
            fence = marker
            lines.append(line)
            continue
        if fence is not None:
            lines.append(line)
            if line.strip().startswith(fence):
                fence = None
            continue
        lines.append(compress_line(line))
    return "\n".join(lines)


def phrases() -> Iterable[tuple[str, str]]:
    """The replacement table, for tests and documentation."""
    return _PHRASES
