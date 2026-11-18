"""Protected-span validation (SPC) for any lossy rewrite of tool output.

Compression is only safe when it removes redundancy, not when it removes the
evidence the agent needs: the failing path, the port, the error text, the
command it must not run, the credential-shaped string it must not leak. The
built-in engine keeps that property by construction (format-aware rules, a
marker, a retained original). Any *additional* optimizer needs a check it can
apply to a candidate output before accepting it, and that check must be
independent of the strategy that produced the candidate.

This module is that check. It extracts the spans that must survive (code,
inline code, URLs, paths, numbers, identifiers, commands, negations, security
terms and error terms) and compares two texts. It is deliberately
conservative: on an unclear case it says "not preserved", and a caller that
cannot prove preservation must fall back to the original text.

Rules:

1. **Counts, not vibes.** For every strict kind (code, URLs, paths, commands,
   negations, security, errors, identifiers) every distinct protected span in
   the original must appear at least as many times in the candidate. Numbers
   are checked by total count only, because a strategy may legitimately merge
   two numbered lines.
2. **Whitespace-canonical matching.** Runs of spaces and tabs are collapsed
   before matching, so a rewrite that re-indents text is not punished for it
   while a rewrite that drops a path still is.
3. **Never raises.** Any internal failure returns ``(False, [...])``: the
   caller keeps the original text. A validator that throws is worse than no
   validator, because the optimizer would fail open into a lossy result.
4. **No I/O, no config, no state.** Pure functions over strings so the check
   can run per candidate inside a bounded search loop and be tested directly.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Kinds where every distinct span must survive verbatim (after whitespace
# canonicalisation). "number" is compared by total count instead.
STRICT_KINDS = (
    "code-fence",
    "inline-code",
    "url",
    "path",
    "command",
    "negation",
    "security",
    "error",
    "identifier",
)
PROTECTED_KINDS = STRICT_KINDS + ("number",)

# Priority for overlap resolution: an earlier kind wins a shared region.
_PRIORITY = {kind: index for index, kind in enumerate(PROTECTED_KINDS)}

_CODE_FENCE = re.compile(r"```[^\n]*\n.*?(?:```|\Z)|~~~[^\n]*\n.*?(?:~~~|\Z)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(
    r"\b(?:https?|ftp|ssh|git)://[^\s<>\"'`]+"
    r"|\bgit@[\w.\-]+:[\w./\-]+"
    r"|\bwww\.[\w.\-]+\.[a-z]{2,}[^\s<>\"'`]*",
    re.I,
)
_PATH = re.compile(
    r"(?<![\w/])(?:~|\.{1,2})?/(?:[\w.\-]+/)*[\w.\-]+"        # POSIX absolute
    r"|(?<![\w/])(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,8}\b"  # relative/path.py
    r"|(?<![\w])[A-Za-z]:\\(?:[\w.\- ]+\\)*[\w.\-]+"          # Windows
)
_COMMAND = re.compile(r"(?m)^[ \t]*(?:\$|>|#|PS>)[ \t]+\S.*$")
_NEGATION_WORDS = (
    r"not|never|no|none|without|cannot|can't|won't|don't|doesn't|isn't|"
    r"aren't|except|avoid|neither|nor|must\s+not|should\s+not|do\s+not|"
    r"does\s+not"
)
_SECURITY_WORDS = (
    r"password|passwd|secret|token|credential|api[_-]?key|access[_-]?key|"
    r"bearer|authorization|cookie|session[_-]?id|private[_-]?key|chmod|chown|"
    r"sudo|ssh-rsa"
)
_ERROR_WORDS = (
    r"error|exception|traceback|fatal|panic|failed|failure|denied|refused|"
    r"timeout|timed\s+out|segfault|abort(?:ed)?"
)
# The three keyword families are one scan: a separate regex per family costs
# three full passes over the text, and this check runs once per candidate.
_KEYWORDS = re.compile(
    rf"\b(?P<negation>{_NEGATION_WORDS})\b"
    rf"|\b(?P<security>{_SECURITY_WORDS})\b"
    rf"|\b(?P<error>{_ERROR_WORDS})\b",
    re.I,
)
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)*(?![\w])")
_IDENTIFIER = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+\b"                # snake_case
    r"|\b[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b"                    # camelCase
    r"|\b[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b"                 # PascalCase
    r"|\b\w+(?:\.\w+){1,}\b"                                   # dotted.name
)
_MAX_SPAN_CHARS = 4000

# (kind, pattern); a None kind means the regex names the kind in its groups.
_PATTERNS: tuple[tuple[str | None, re.Pattern[str]], ...] = (
    ("code-fence", _CODE_FENCE),
    ("inline-code", _INLINE_CODE),
    ("url", _URL),
    ("path", _PATH),
    ("command", _COMMAND),
    (None, _KEYWORDS),
    ("number", _NUMBER),
    ("identifier", _IDENTIFIER),
)


@dataclass(frozen=True)
class Span:
    """One protected region of a text."""

    kind: str
    text: str
    start: int = 0
    end: int = 0


def _canonical(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text)


def _analyse(text: str) -> tuple[tuple[Span, ...], Counter[tuple[str, str]]]:
    """Extract spans and their multiset in one pass over the canonical text."""
    canonical = _canonical(text)
    found: list[Span] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(canonical):
            body = match.group(0)
            if not body or len(body) > _MAX_SPAN_CHARS:
                continue
            found.append(
                Span(kind or match.lastgroup or "identifier", body,
                     match.start(), match.end())
            )
    found.sort(key=lambda span: (span.start, _PRIORITY.get(span.kind, 99)))
    accepted: list[Span] = []
    counter: Counter[tuple[str, str]] = Counter()
    last_end = -1
    for span in found:
        if span.start < last_end:
            # Overlaps the span already accepted at this position; the
            # higher-priority/earlier span owns the region.
            continue
        accepted.append(span)
        counter[(span.kind, span.text)] += 1
        last_end = span.end
    return tuple(accepted), counter


def protected_spans(text: str) -> tuple[Span, ...]:
    """Return the protected spans of ``text`` in document order.

    Overlapping spans keep the highest-priority kind (see ``PROTECTED_KINDS``)
    and the earlier start; a span fully inside an accepted one is dropped.
    """
    if not isinstance(text, str) or not text:
        return ()
    return _analyse(text)[0]


def counts(text: str) -> dict[str, int]:
    """Number of protected spans of each kind in ``text``."""
    if not isinstance(text, str) or not text:
        return {kind: 0 for kind in PROTECTED_KINDS}
    _, counter = _analyse(text)
    totals: Counter[str] = Counter()
    for (kind, _body), number in counter.items():
        totals[kind] += number
    return {kind: totals.get(kind, 0) for kind in PROTECTED_KINDS}


def describe(text: str) -> dict[str, object]:
    """Human-readable summary used by diagnostics and benchmark reports."""
    spans = protected_spans(text)
    counter: Counter[str] = Counter(span.kind for span in spans)
    return {
        "total": len(spans),
        "by_kind": {kind: counter.get(kind, 0) for kind in PROTECTED_KINDS},
        "longest": max((len(span.text) for span in spans), default=0),
    }


def missing(original: str, candidate: str) -> dict[str, list[str]]:
    """Protected span texts present in ``original`` but not in ``candidate``.

    The return value maps kind to the missing texts (deduplicated, in document
    order). An empty mapping means every strict span survived and the number
    count did not drop.
    """
    if not isinstance(original, str) or not isinstance(candidate, str):
        return {"input": ["non-text input"]}
    return _compare(_analyse(original), _analyse(candidate))


def _compare(
    original: tuple[tuple[Span, ...], Counter[tuple[str, str]]],
    candidate: tuple[tuple[Span, ...], Counter[tuple[str, str]]],
) -> dict[str, list[str]]:
    original_spans, orig_counter = original
    _, cand_counter = candidate
    result: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for span in original_spans:
        key = (span.kind, span.text)
        if key in seen or span.kind == "number":
            continue
        seen.add(key)
        if cand_counter.get(key, 0) < orig_counter[key]:
            result.setdefault(span.kind, []).append(span.text)
    original_numbers = sum(
        number for (kind, _body), number in orig_counter.items()
        if kind == "number"
    )
    candidate_numbers = sum(
        number for (kind, _body), number in cand_counter.items()
        if kind == "number"
    )
    if candidate_numbers < original_numbers:
        result.setdefault("number", []).append(
            f"{original_numbers} -> {candidate_numbers}"
        )
    return result


def preservation_ok(original: str, candidate: str) -> tuple[bool, list[str]]:
    """Whether ``candidate`` may replace ``original``.

    Returns ``(ok, reasons)``. ``ok`` is True only when the candidate is
    non-empty, is not larger than the original, and preserves every protected
    span under the rules in the module docstring. ``reasons`` is empty when
    ``ok`` is True and otherwise names what was lost, per kind.
    """
    try:
        if not isinstance(candidate, str) or not candidate:
            return False, ["candidate is empty"]
        if not isinstance(original, str):
            return False, ["original is not text"]
        if len(candidate) > len(original):
            return False, [
                f"candidate larger than original "
                f"({len(candidate)} > {len(original)} bytes)"
            ]
        if candidate == original:
            return True, []
        lost = _compare(_analyse(original), _analyse(candidate))
        if not lost:
            return True, []
        reasons = []
        for kind, texts in lost.items():
            sample = texts[0]
            suffix = f" (+{len(texts) - 1} more)" if len(texts) > 1 else ""
            if kind == "number":
                reasons.append(f"number: dropped ({sample})")
            else:
                reasons.append(f"{kind}: lost {sample!r}{suffix}")
        return False, reasons
    except Exception:  # pragma: no cover - defensive: never break a caller
        logger.debug("sutra: preservation check failed", exc_info=True)
        return False, ["internal error in preservation check"]
