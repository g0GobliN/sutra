"""Deterministic, opt-in wording compression for prose lines.

The rule is narrow: remove words that carry no technical meaning, never
rewrite code, and let the protected-span validator decide whether the result is
acceptable. It is deterministic — a
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
    ("in light of the fact that", "because"),
    ("notwithstanding the fact that", "although"),
    ("in spite of the fact that", "although"),
    ("for the purpose of", "to"),
    ("in the event that", "if"),
    ("at this point in time", "now"),
    ("with regard to", "about"),
    ("in order to", "to"),
    ("has the ability to", "can"),
    ("is able to", "can"),
    ("a large number of", "many"),
    ("a number of", "some"),
    ("on a regular basis", "regularly"),
    ("in the near future", "soon"),
    ("in the process of", ""),
    ("it should be noted that", ""),
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


# A duplicate sentence is only dropped when it is long enough to be a real
# restatement rather than a list item or a heading repeated for layout.
_MIN_DUPLICATE_SENTENCE = 32
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _dedupe_words(text: str) -> str:
    """Collapse an immediately repeated word ("the the")."""
    return re.sub(r"\b([A-Za-z][A-Za-z'-]{1,})\s+\1\b", r"\1", text,
                  flags=re.IGNORECASE)


def _dedupe_sentences(text: str) -> str:
    """Drop an exact repeated sentence inside one prose line.

    Exactness is the guarantee: the removed sentence is byte-identical after
    trimming to a sentence already present on the same line, so no content is
    paraphrased or inferred. Short sentences are kept because they are usually
    list items where a repeat is intentional.
    """
    sentences = _SENTENCE_SPLIT.split(text)
    if len(sentences) < 2:
        return text
    seen: set[str] = set()
    kept: list[str] = []
    changed = False
    for sentence in sentences:
        key = sentence.strip()
        if len(key) >= _MIN_DUPLICATE_SENTENCE and key in seen:
            changed = True
            continue
        if len(key) >= _MIN_DUPLICATE_SENTENCE:
            seen.add(key)
        kept.append(sentence)
    if not changed:
        return text
    return " ".join(kept)


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
    out = _dedupe_words(out)
    out = _dedupe_sentences(out)
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


def compress_tool_description(text: str) -> str:
    """Compact one tool description while keeping it parseable text.

    Tool descriptions are prose the host exposes as metadata; this is the same
    deterministic wording pass with one difference: a quoted phrase in a
    description is prose, so quotes do not disqualify the line. Template
    placeholders and fenced blocks are still refused. The caller must run
    ``preserve.preservation_ok`` before publishing the result, exactly as the
    text path does.
    """
    if not isinstance(text, str) or not text:
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
        if any(hint in line for hint in ("${", "{{", "}}")) or "`" in line:
            lines.append(line)
            continue
        out = line.strip(" ")
        for pattern, replacement in _COMPILED:
            out = pattern.sub(lambda m, r=replacement: _keep_case(m, r), out)
        out = _squeeze(_dedupe_sentences(_dedupe_words(out)))
        lines.append(out if out else line)
    return "\n".join(lines)


def phrases() -> Iterable[tuple[str, str]]:
    """The replacement table, for tests and documentation."""
    return _PHRASES
