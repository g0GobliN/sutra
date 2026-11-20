"""Deterministic, opt-in wording compression for prose lines.

Three narrow passes, all of them strict rewrites that keep the sentence's
grammatical skeleton: a table of verbose phrases, a subordination compaction
(", which means that X" becomes ", meaning X") and a sentence-opener removal
("Note that X" becomes "X"). They never rewrite code, and the protected-span
validator still decides whether the result is acceptable. Everything is
deterministic, and a line is refused when it looks like code, contains a quote
or a template placeholder, or sits inside a fenced block.

Deliberately *not* implemented: paraphrasing, synonym selection, clause
reordering, dropping whole sentences. Those need a model and a semantic
guarantee this project does not have; claiming them from a word table would be
dishonest. Every candidate must still pass ``preserve.preservation_ok``
(negations, numbers, identifiers, commands, paths, URLs, error and security
terms) before the optimizer may apply it.
"""

from __future__ import annotations

import re
from typing import Iterable

# Longest phrase first: "in order to" must win over "in order", and every
# "...the fact that" variant must be rewritten before the generic one.
_PHRASES: tuple[tuple[str, str], ...] = (
    ("due to the fact that", "because"),
    ("in light of the fact that", "because"),
    ("owing to the fact that", "because"),
    ("because of the fact that", "because"),
    ("notwithstanding the fact that", "although"),
    ("in spite of the fact that", "although"),
    ("despite the fact that", "although"),
    ("for the reason that", "because"),
    ("in the majority of cases", "usually"),
    ("the vast majority of", "most"),
    ("a significant number of", "many"),
    ("in close proximity to", "near"),
    ("with the exception of", "except"),
    ("in the absence of", "without"),
    ("in conjunction with", "with"),
    ("with the aim of", "to"),
    ("conduct an investigation into", "investigate"),
    ("perform an analysis of", "analyze"),
    ("take into consideration", "consider"),
    ("give consideration to", "consider"),
    ("in such a way that", "so that"),
    ("at the present time", "now"),
    ("at this point in time", "now"),
    ("in the near future", "soon"),
    ("on the other hand", "however"),
    ("as a result of", "from"),
    ("with regard to", "about"),
    ("with respect to", "about"),
    ("in relation to", "about"),
    ("for the purpose of", "to"),
    ("in the event that", "if"),
    ("in order that", "so"),
    ("in order to", "to"),
    ("has the ability to", "can"),
    ("have the ability to", "can"),
    ("will be able to", "can"),
    ("is able to", "can"),
    ("is going to", "will"),
    ("are going to", "will"),
    ("make use of", "use"),
    # No ("make sure to", "ensure"): "ensure clear the cache" is ungrammatical,
    # and fixing it needs a verb-phrase rewrite this table cannot express.
    ("make a decision", "decide"),
    ("provide assistance", "help"),
    ("a large number of", "many"),
    ("a small number of", "few"),
    ("a majority of", "most"),
    ("a number of", "some"),
    ("on a regular basis", "regularly"),
    ("on account of", "because of"),
    ("by means of", "via"),
    ("prior to", "before"),
    ("subsequent to", "after"),
    ("so as to", "to"),
    ("as well as", "and"),
    ("for this reason", "so"),
    ("in the process of", ""),
    ("it should be noted that", ""),
    ("it is important to note that", ""),
    ("please note that", ""),
    ("as a matter of fact", ""),
    ("could you please", ""),
    ("i would like you to", ""),
    # Generic, so it runs after every specific "...the fact that" phrase.
    ("the fact that", "that"),
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

_PHRASE_ALTERNATION = "|".join(
    re.escape(phrase)
    for phrase, _ in sorted(_PHRASES, key=lambda entry: len(entry[0]), reverse=True)
)
# One alternation instead of one pattern per phrase: a line is scanned once,
# not once per entry (the table is ~60 entries, and the orchestrator calls this
# for every line of every candidate). Longest phrase first, so "a small number
# of" is not pre-empted by "a number of".
_COMPILED = re.compile(
    r"(?<![\w-])(?:" + _PHRASE_ALTERNATION + r")(?![\w-])", re.IGNORECASE
)
_PHRASE_REPLACEMENTS = {phrase.lower(): replacement for phrase, replacement in _PHRASES}

# A relative clause whose whole meaning is the conjunction: "X, which means
# that Y" states the same thing as "X, meaning Y". The comma is required, so a
# list item that merely contains the words is left alone.
_SUBORDINATE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r",\s+which means that\s+", re.IGNORECASE), ", meaning "),
    (re.compile(r",\s+and that means\s+", re.IGNORECASE), ", so "),
    (re.compile(r",\s+which is why\s+", re.IGNORECASE), ", so "),
    (re.compile(r",\s+which means\s+", re.IGNORECASE), ", meaning "),
)

# A line-opening reminder carries no instruction of its own. Anchored to the
# start of the line, so "the note that ..." is never touched.
_OPENER = re.compile(
    r"^(?:also\s+|pls\s+)?(?:note|remember|keep in mind|bear in mind|be aware)"
    r"\s+that\s+",
    re.IGNORECASE,
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


def _phrase(match: re.Match[str]) -> str:
    """Replace one table hit, keeping the original starting capital."""
    return _keep_case(match, _PHRASE_REPLACEMENTS[match.group(0).lower()])


def _rewrite(text: str) -> str:
    """Apply the three passes, then the duplicate and whitespace cleanups."""
    out = text
    # A single pass is the normal case. Repeating a bounded number of times
    # reproduces the cascade the per-phrase loop allowed, where one
    # replacement exposed another entry ("on account of" -> "because of").
    for _ in range(3):
        rewritten = _COMPILED.sub(_phrase, out)
        if rewritten == out:
            break
        out = rewritten
    for pattern, replacement in _SUBORDINATE:
        out = pattern.sub(replacement, out)
    out = _OPENER.sub("", out)
    out = _dedupe_words(out)
    out = _dedupe_sentences(out)
    return _squeeze(out)


def _rewrite_line(text: str) -> str:
    """Rewrite one trimmed line, keeping a capitalised line starting capital."""
    capitalised_start = text[:1].isupper()
    out = _rewrite(text)
    if out and capitalised_start and out[:1].islower():
        out = out[:1].upper() + out[1:]
    return out


def compress_line(line: str) -> str:
    """Rewrite one prose line; never raises, never returns a non-str."""
    stripped = line.strip()
    if not stripped or stripped.startswith(_MARKER_PREFIX):
        return line
    if any(hint in line for hint in _CODE_HINTS):
        return line
    indent = line[:len(line) - len(line.lstrip(" "))]
    out = _rewrite_line(line.strip(" "))
    if not out:
        return line
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
        out = _rewrite_line(line.strip(" "))
        lines.append(out if out else line)
    return "\n".join(lines)


def phrases() -> Iterable[tuple[str, str]]:
    """The replacement table, for tests and documentation."""
    return _PHRASES
