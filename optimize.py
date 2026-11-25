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
3. **Bounded effort.** At most ``max_passes`` passes. The pass count is the
   bound, not a wall-clock deadline: a clock-based stop made the applied
   strategy depend on host load, so ``max_ms`` is accepted for compatibility
   and recorded but no longer stops the loop.
4. **Deterministic pairing bound.** Combination search is skipped above
   ``PAIR_MAX_BYTES`` (256 KiB); the bound is a size, not a clock, so the same
   payload always gets the same treatment.
5. **Never raise.** A strategy that throws or returns a non-string, a failing
   validator and an empty candidate are recorded as rejected evaluations; the
   caller still gets the original text.
6. **Deterministic.** Strategies run in a fixed order, ranking is
   ``scoring.seof`` over the measured fraction saved with a fixed nominal
   overhead and the strategy name as tie-break, and the same input always
   produces the same selection. Measured milliseconds are reported per
   attempt but never decide the winner: on a loaded host two identical runs
   otherwise pick different strategies of nearly equal size.

Recovery markers (lines whose stripped form starts with ``[sutra:``) and
fenced code blocks are never rewritten: the first is the recovery contract and
the second is text whose exact bytes may matter.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from . import failure, preserve, scoring

logger = logging.getLogger(__name__)

_FENCES = ("```", "~~~")
_MARKER_PREFIX = "[sutra:"
_MAX_REASON_CHARS = 200

# Ranking uses a fixed nominal overhead, not the measured elapsed time: with
# real timings two identical runs can rank two similarly sized candidates
# differently, which makes the output depend on host load. Timings are still
# measured and reported; they just do not decide the winner. The value is a
# ratio, not milliseconds: the saving it is subtracted from is
# ``saved_bytes / original_bytes``, so both terms stay dimensionless and SEOF
# never adds bytes to a clock reading.
RANKING_OVERHEAD = 0.0
# Combination search multiplies the work by the number of ordered pairs. The
# cap is a payload size, not a clock, so the decision stays deterministic.
PAIR_MAX_BYTES = 256 * 1024
# Triple combinations are only tried when no pair was accepted and the payload
# is small; the size bound keeps the choice deterministic and bounded.
TRIPLE_MAX_BYTES = 64 * 1024


def _fraction(saved_bytes: int, original_bytes: int) -> float:
    """The saving as a dimensionless ratio, the unit SEOF is scored in."""
    return saved_bytes / max(1, original_bytes)


def _seof(saved_bytes: int, original_bytes: int) -> float:
    """SEOF over one candidate, with both terms in the same (ratio) unit."""
    return scoring.seof(_fraction(saved_bytes, original_bytes),
                        RANKING_OVERHEAD)


def _size(text: str) -> int:
    """UTF-8 byte length, the unit the savings fields are named in.

    ``len()`` counts code points, so a payload with non-ASCII text reported a
    saving larger than the bytes a provider would see. Every acceptance
    decision and every recorded saving uses this function.
    """
    return len(text.encode("utf-8", "surrogatepass"))


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
    saved = _size(text) - _size(candidate)
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


class _Applied:
    """Memoise strategy applications for one input text.

    The combination phases run every ordered chain over the same text, so the
    first strategy of a pair was applied once per second strategy and a triple
    re-applied its two-strategy prefix once per third strategy. The strategies
    are pure functions of their input, so applying a prefix once and reusing
    its text leaves the candidates, their order and their ranking exactly as
    they were.

    The memo is valid for one text only: a call with a different base empties
    it instead of answering with text computed from the old one.
    """

    def __init__(self, base: str) -> None:
        self._base = base
        self._done: dict[tuple[int, ...], tuple[str | None, str]] = {}

    def chain(
        self, base: str, strategies: Sequence[Strategy],
    ) -> tuple[str | None, str]:
        """Return the text after ``strategies`` applied to ``base``.

        A cached chain is only valid for the text it was built from, so the
        base is compared by value: an equal copy of the text answers from the
        cache, and a different text empties it instead of answering from it.
        """
        if base != self._base:
            self._base = base
            self._done.clear()
        # Identity keys: two strategies may share a name, and a name shared by
        # a different strategy would otherwise answer with the wrong text.
        key = tuple(id(item) for item in strategies)
        if key in self._done:
            return self._done[key]
        if len(strategies) == 1:
            strategy = strategies[0]
            try:
                text = strategy.apply(base)
            except Exception as exc:
                result: tuple[str | None, str] = (None, f"strategy raised: {exc}")
            else:
                result = (text, "") if isinstance(text, str) and text else (
                    None, f"{strategy.name} produced no text")
            self._done[key] = result
            return result
        prefix, reason = self.chain(base, strategies[:-1])
        if prefix is None:
            self._done[key] = (None, reason)
            return self._done[key]
        strategy = strategies[-1]
        try:
            text = strategy.apply(prefix)
        except Exception as exc:
            result = (None, f"strategy raised: {exc}")
        else:
            result = (text, "") if isinstance(text, str) and text else (
                None, f"{strategy.name} produced no text")
        self._done[key] = result
        return result


def _evaluate_chain(
    base: str,
    original: str,
    strategies: Sequence[Strategy],
    validator: Callable[[str, str], tuple[bool, list[str]]],
    min_saved_bytes: int,
    applied: _Applied,
) -> tuple[Evaluation, str | None]:
    """Apply an ordered strategy chain, validating against the run's original.

    Combination search exists because two transformations can each save
    nothing alone while the pair still shrinks the payload. Validating the
    final text against the text at the start of the run keeps the cumulative
    guarantee the single-strategy path has.
    """
    name = "+".join(item.name for item in strategies)
    started = time.monotonic()
    candidate, reason = applied.chain(base, strategies)
    if candidate is None:
        elapsed = (time.monotonic() - started) * 1000.0
        return Evaluation(name, 0, elapsed, False, reason), None
    elapsed = (time.monotonic() - started) * 1000.0
    saved = _size(base) - _size(candidate)
    try:
        ok, reasons = validator(original, candidate)
    except Exception as exc:
        return Evaluation(name, saved, elapsed, False,
                          f"validator raised: {exc}"), None
    if not ok:
        reason = (reasons[0] if reasons else "validator rejected")
        return Evaluation(name, saved, elapsed, False,
                          reason[:_MAX_REASON_CHARS]), None
    if saved < min_saved_bytes:
        return Evaluation(
            name, saved, elapsed, False,
            f"saved {saved} bytes, below the {min_saved_bytes}-byte minimum",
        ), None
    return Evaluation(name, saved, elapsed, True), candidate


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
    ledger: failure.Ledger | None = None,
    combine: bool = False,
    triple: bool = False,
) -> Report:
    """Run the bounded optimization loop over ``text``.

    Returns a report whose ``text`` is the original when nothing was accepted.
    Non-text input and zero budgets return the original text unchanged. A
    ``ledger`` scopes repeated hard failures to one payload shape and skips a
    strategy that keeps failing there.
    """
    if not isinstance(text, str) or not text:
        return _empty_report()
    if max_passes <= 0:
        return _empty_report(text)
    candidates = tuple(strategies) if strategies is not None else default_strategies()
    if not candidates:
        return _empty_report(text)
    check = validator if validator is not None else preserve.preservation_ok
    started = time.monotonic()
    scope = failure.shape(text) if ledger is not None else ""
    current = text
    evaluations: list[Evaluation] = []
    applied: list[str] = []

    for _ in range(max_passes):
        accepted: list[tuple[Evaluation, str]] = []
        for strategy in candidates:
            if ledger is not None and ledger.is_disabled(strategy.name, scope):
                evaluations.append(Evaluation(
                    strategy.name, 0, 0.0, False,
                    "skipped: disabled for this payload shape after repeated failures"))
                continue
            evaluation, candidate = _evaluate(
                current, strategy, check, min_saved_bytes)
            evaluations.append(evaluation)
            if candidate is not None:
                accepted.append((evaluation, candidate))
        if not accepted:
            if combine and _size(current) <= PAIR_MAX_BYTES:
                # One cache serves both phases: the triple phase runs only when
                # no pair was accepted (``if not pairs``), so ``current`` cannot
                # have moved between them. A caller that did pass another text
                # would still be safe: the cache empties itself on a new base.
                prefixes = _Applied(current)
                pairs: list[tuple[Evaluation, str]] = []
                for first in candidates:
                    for second in candidates:
                        if first is second:
                            continue
                        pair_evaluation, pair_text = _evaluate_chain(
                            current, text, (first, second), check,
                            min_saved_bytes, prefixes)
                        evaluations.append(pair_evaluation)
                        if pair_text is not None:
                            pairs.append((pair_evaluation, pair_text))
                if not pairs and triple and _size(current) <= TRIPLE_MAX_BYTES:
                    triples: list[tuple[Evaluation, str]] = []
                    for first in candidates:
                        for second in candidates:
                            if second is first:
                                continue
                            for third in candidates:
                                if third is first or third is second:
                                    continue
                                combo_evaluation, combo_text = _evaluate_chain(
                                    current, text, (first, second, third),
                                    check, min_saved_bytes, prefixes)
                                evaluations.append(combo_evaluation)
                                if combo_text is not None:
                                    triples.append((combo_evaluation, combo_text))
                    if triples:
                        best, best_text = min(
                            triples,
                            key=lambda item: (
                                -_seof(item[0].saved_bytes, _size(text)),
                                item[0].name,
                            ),
                        )
                        current = best_text
                        applied.append(best.name)
                        continue
                if pairs:
                    best, best_text = min(
                        pairs,
                        key=lambda item: (
                            -_seof(item[0].saved_bytes, _size(text)),
                            item[0].name,
                        ),
                    )
                    current = best_text
                    applied.append(best.name)
                    continue
            break
        best, best_text = min(
            accepted,
            key=lambda item: (
                -_seof(item[0].saved_bytes, _size(text)),
                item[0].name,
            ),
        )
        current = best_text
        applied.append(best.name)

    elapsed_ms = (time.monotonic() - started) * 1000.0
    failure.record_attempts(ledger, text, evaluations)
    return Report(
        text=current,
        evaluations=tuple(evaluations),
        applied=tuple(applied),
        elapsed_ms=elapsed_ms,
        saved_bytes=max(0, _size(text) - _size(current)),
    )
