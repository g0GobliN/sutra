"""Bounded maximum-effort post-compression optimization.

The engine makes one deterministic pass. This layer asks a different question:
given the text that pass produced, is there anything else that is strictly
smaller and provably safe? It tries a small set of formatting strategies,
validates every candidate against the text at the start of the run with
``preserve.preservation_ok`` (paths, URLs, numbers, identifiers, commands,
negations, error and security terms all survive), applies at most the best
candidate per pass, and stops on a pass cap or a time budget.

Rules, in the same priority order as the rest of the project:

1. **Nothing is removed.** The strategies only normalise line endings, unicode
   spaces, trailing spaces, blank-line runs and deep indentation. Content
   removal is the engine's job and it needs a recovery marker; this layer adds
   none, so it removes no content.
2. **Validator first.** A candidate is applied only when the validator accepts
   it, and the validator sees the text at the start of the run, so several
   accepted passes cannot hide a cumulative loss.
3. **Bounded effort.** At most ``max_passes`` passes and ``max_ms``
   milliseconds; both are checked between strategies so one large payload
   cannot stall a tool result.
4. **Never raise.** A strategy that throws or returns a non-string, a failing
   validator and an empty candidate are recorded as rejected evaluations; the
   caller still gets the original text.
5. **Deterministic.** Strategies run in a fixed order, ranking is
   ``saved_bytes / (1 + elapsed_ms)`` with the strategy name as tie-break, and
   the same input always produces the same selection.

Recovery markers (lines whose stripped form starts with ``[sutra:``) and
fenced code blocks are never rewritten: the first is the recovery contract and
the second is text whose exact bytes may matter.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from . import preserve

logger = logging.getLogger(__name__)

_FENCES = ("```", "~~~")
_MARKER_PREFIX = "[sutra:"
_MAX_REASON_CHARS = 200


@dataclass(frozen=True)
class Strategy:
    """One deterministic, content-preserving transformation."""

    name: str
    apply: Callable[[str], str]
    note: str = ""


@dataclass(frozen=True)
class Evaluation:
    """One strategy tried on one pass, accepted or not."""

    name: str
    saved_bytes: int
    elapsed_ms: float
    accepted: bool
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "saved_bytes": self.saved_bytes,
            "elapsed_ms": round(self.elapsed_ms, 6),
            "accepted": self.accepted,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Report:
    """The outcome of an optimization run, with per-candidate evidence."""

    text: str
    evaluations: tuple[Evaluation, ...]
    applied: tuple[str, ...]
    elapsed_ms: float
    saved_bytes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "applied": list(self.applied),
            "passes": len(self.applied),
            "saved_bytes": self.saved_bytes,
            "elapsed_ms": round(self.elapsed_ms, 6),
            "evaluations": [item.as_dict() for item in self.evaluations],
        }


def _outside_fence_lines(text: str) -> list[str]:
    """Split into lines, marking each as "may be rewritten".

    Fence marker lines and everything between a fence opener and its closer
    carry a trailing space, and marker lines carry nothing, so the returned
    list holds the line and whether a strategy may touch it.
    """
    marked: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        head = stripped[:3]
        if fence is None and head in _FENCES:
            fence = head
            marked.append(line + "\0inside")
            continue
        if fence is not None:
            marked.append(line + "\0inside")
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith(_MARKER_PREFIX):
            marked.append(line + "\0inside")
            continue
        marked.append(line + "\0outside")
    return marked


def _rewrite_outside(text: str, transform: Callable[[str], str]) -> str:
    """Apply ``transform`` only to rewrite-safe lines, preserving the rest."""
    out: list[str] = []
    for marked in _outside_fence_lines(text):
        line, _, state = marked.rpartition("\0")
        out.append(transform(line) if state == "outside" else line)
    return "\n".join(out)


def _apply_crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


_UNICODE_SPACES = (
    ("\u00a0", " "),
    ("\u2007", " "),
    ("\u202f", " "),
    ("\u2009", " "),
    ("\u200b", ""),
)


def _apply_unicode_space(text: str) -> str:
    for old, new in _UNICODE_SPACES:
        text = text.replace(old, new)
    return text


def _apply_trailing_space(text: str) -> str:
    return _rewrite_outside(text, lambda line: line.rstrip(" \t"))


def _apply_blank_runs(text: str) -> str:
    out: list[str] = []
    run = 0
    for marked in _outside_fence_lines(text):
        line, _, state = marked.rpartition("\0")
        if state != "outside" or line.strip() != "":
            out.append(line)
            run = 0
            continue
        run += 1
        if run <= 2:
            out.append(line)
    return "\n".join(out)


def _trim_indent(line: str) -> str:
    stripped = line.lstrip(" ")
    lead = len(line) - len(stripped)
    if lead <= 8:
        return line
    return " " * 8 + stripped


def _apply_indent_trim(text: str) -> str:
    return _rewrite_outside(text, _trim_indent)


def default_strategies() -> tuple[Strategy, ...]:
    """The built-in strategies, in their fixed evaluation order."""
    return (
        Strategy("crlf", _apply_crlf,
                 "normalise CRLF and stray CR to LF"),
        Strategy("unicode-space", _apply_unicode_space,
                 "replace unicode spaces and zero-width spaces"),
        Strategy("trailing-space", _apply_trailing_space,
                 "strip trailing spaces outside fenced blocks"),
        Strategy("blank-runs", _apply_blank_runs,
                 "collapse runs of 3+ blank lines outside fenced blocks"),
        Strategy("indent-trim", _apply_indent_trim,
                 "cap leading indentation at 8 spaces outside fenced blocks"),
    )


def _evaluate(
    text: str,
    strategy: Strategy,
    validator: Callable[[str, str], tuple[bool, list[str]]],
    min_saved_bytes: int,
) -> tuple[Evaluation, str | None]:
    """Try one strategy; return its evaluation and the candidate if accepted."""
    started = time.monotonic()
    try:
        candidate = strategy.apply(text)
    except Exception as exc:  # a broken strategy must not break the loop
        elapsed = (time.monotonic() - started) * 1000.0
        return Evaluation(strategy.name, 0, elapsed, False,
                          f"strategy raised: {exc}"), None
    elapsed = (time.monotonic() - started) * 1000.0
    if not isinstance(candidate, str):
        return Evaluation(strategy.name, 0, elapsed, False,
                          "candidate is not text"), None
    if not candidate:
        return Evaluation(strategy.name, 0, elapsed, False,
                          "candidate is empty"), None
    saved = len(text) - len(candidate)
    try:
        ok, reasons = validator(text, candidate)
    except Exception as exc:  # a broken validator rejects, never crashes
        return Evaluation(strategy.name, saved, elapsed, False,
                          f"validator raised: {exc}"), None
    if not ok:
        reason = (reasons[0] if reasons else "validator rejected")
        return Evaluation(strategy.name, saved, elapsed, False,
                          reason[:_MAX_REASON_CHARS]), None
    if saved < min_saved_bytes:
        return Evaluation(
            strategy.name, saved, elapsed, False,
            f"saved {saved} bytes, below the {min_saved_bytes}-byte minimum",
        ), None
    return Evaluation(strategy.name, saved, elapsed, True), candidate


def _empty_report(text: str = "") -> Report:
    return Report(text=text, evaluations=(), applied=(), elapsed_ms=0.0,
                  saved_bytes=0)


def optimize(
    text: str,
    *,
    max_passes: int = 4,
    max_ms: float = 50.0,
    min_saved_bytes: int = 1,
    strategies: Sequence[Strategy] | None = None,
    validator: Callable[[str, str], tuple[bool, list[str]]] | None = None,
) -> Report:
    """Run the bounded optimization loop over ``text``.

    Returns a report whose ``text`` is the original when nothing was accepted.
    Non-text input and zero budgets return the original text unchanged.
    """
    if not isinstance(text, str) or not text:
        return _empty_report()
    if max_passes <= 0 or max_ms <= 0:
        return _empty_report(text)
    candidates = tuple(strategies) if strategies is not None else default_strategies()
    if not candidates:
        return _empty_report(text)
    check = validator if validator is not None else preserve.preservation_ok
    started = time.monotonic()
    current = text
    evaluations: list[Evaluation] = []
    applied: list[str] = []

    for _ in range(max_passes):
        if (time.monotonic() - started) * 1000.0 >= max_ms:
            break
        accepted: list[tuple[Evaluation, str]] = []
        for strategy in candidates:
            if (time.monotonic() - started) * 1000.0 >= max_ms:
                break
            evaluation, candidate = _evaluate(
                current, strategy, check, min_saved_bytes)
            evaluations.append(evaluation)
            if candidate is not None:
                accepted.append((evaluation, candidate))
        if not accepted:
            break
        best, best_text = min(
            accepted,
            key=lambda item: (
                -(item[0].saved_bytes / (1.0 + item[0].elapsed_ms)),
                item[0].name,
            ),
        )
        current = best_text
        applied.append(best.name)

    elapsed_ms = (time.monotonic() - started) * 1000.0
    return Report(
        text=current,
        evaluations=tuple(evaluations),
        applied=tuple(applied),
        elapsed_ms=elapsed_ms,
        saved_bytes=max(0, len(text) - len(current)),
    )
