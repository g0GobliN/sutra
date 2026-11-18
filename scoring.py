"""SUTRA's optimization model: SROF, SEOF, SACE and SMESP.

The engine makes one deterministic pass and the search layer in ``optimize.py``
tries bounded candidates. This module supplies the decision functions those
passes depend on, and it is deliberately
pure: no I/O, no configuration, no global state, so the loop can call it per
candidate and a test can call it directly.

The four functions and what they mean here:

* **SROF** — ``net_benefit(savings, overhead)`` is the expected resource
  benefit of an action minus the cost of finding and validating it. A
  candidate with no demonstrated benefit is not applied.
* **SEOF** — ``seof`` is the quality-weighted savings over optimization
  overhead and reliability risk. In the search loop the inputs
  are the candidate's measured byte saving and its measured elapsed time, so
  the score is evidence, not an estimate.
* **SACE** — ``sace_select`` chooses the lowest expected total cost among the
  candidates that satisfy the quality floor, risk ceiling and latency limit.
  No admissible candidate returns ``None``; callers fall back
  to the original text.
* **SMESP** — ``priority`` ranks candidates by applicability times expected
  savings times reuse value over search overhead. It is a
  ranking signal, not a cutoff: a candidate with a tiny probability still gets
  evaluated when the caller can afford to evaluate it.

Normalisation: every input is in a caller-chosen comparable unit (bytes,
tokens, milliseconds or currency) and the functions preserve that unit. They
do not convert between units, because doing so without a price or tokenizer
would invent a number. ``seof`` divides by ``1 + overhead + risk`` so the
denominator is never zero; probabilities are validated to lie in [0, 1].

SPC lives in ``preserve.py``: admissibility is decided by exact protected-span
comparison before any candidate reaches these functions, and ``min_quality``
is the caller's confidence floor for the semantic half of the constraint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "Candidate",
    "Selection",
    "net_benefit",
    "seof",
    "priority",
    "sace_select",
    "pareto_front",
    "dominates",
]


def _number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result


def _non_negative(name: str, value: float) -> float:
    result = _number(name, value)
    if result < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return result


def _probability(name: str, value: float) -> float:
    result = _number(name, value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return result


@dataclass(frozen=True)
class Candidate:
    """One optimization action with the inputs the model needs.

    ``savings`` and ``overhead`` share a unit. ``cost`` is the expected total
    resource cost of the action in SACE's unit (any consistent cost unit).
    ``quality`` is preservation confidence and ``risk`` is the probability of
    a constraint violation; both are probabilities.
    """

    name: str
    savings: float = 0.0
    overhead: float = 0.0
    quality: float = 1.0
    risk: float = 0.0
    latency_ms: float = 0.0
    cost: float = 0.0
    applicable: float = 1.0
    reuse: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        _non_negative("savings", self.savings)
        _non_negative("overhead", self.overhead)
        _non_negative("latency_ms", self.latency_ms)
        _non_negative("cost", self.cost)
        _non_negative("reuse", self.reuse)
        _probability("quality", self.quality)
        _probability("risk", self.risk)
        _probability("applicable", self.applicable)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "savings": self.savings,
            "overhead": self.overhead,
            "quality": self.quality,
            "risk": self.risk,
            "latency_ms": self.latency_ms,
            "cost": self.cost,
            "applicable": self.applicable,
            "reuse": self.reuse,
        }


@dataclass(frozen=True)
class Selection:
    """The SACE result and the candidates it rejected."""

    chosen: Candidate
    rejected: tuple[Candidate, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "chosen": self.chosen.name,
            "rejected": [item.name for item in self.rejected],
        }


def net_benefit(savings: float, overhead: float) -> float:
    """SROF: B(a) = ΔC(a) - O(a), in the unit of both inputs."""
    return _non_negative("savings", savings) - _non_negative("overhead", overhead)


def seof(savings: float, overhead: float, *, quality: float = 1.0,
         risk: float = 0.0) -> float:
    """SEOF: Q · ΔC / (1 + O + R).

    ``savings`` stands for the sum of the applicable saving terms. The
    denominator's ``1 +`` keeps a zero-overhead, zero-risk candidate finite.
    """
    value = _non_negative("savings", savings)
    cost = _non_negative("overhead", overhead)
    q = _probability("quality", quality)
    r = _probability("risk", risk)
    return q * value / (1.0 + cost + r)


def priority(savings: float, overhead: float, *, applicable: float = 1.0,
             reuse: float = 1.0) -> float:
    """SMESP: P_applicable · E[ΔC] · V_reuse / (1 + O_search)."""
    value = _non_negative("savings", savings)
    cost = _non_negative("overhead", overhead)
    p = _probability("applicable", applicable)
    v = _non_negative("reuse", reuse)
    return p * value * v / (1.0 + cost)


def sace_select(
    candidates: tuple[Candidate, ...] | list[Candidate],
    *,
    min_quality: float = 0.0,
    max_risk: float = 1.0,
    max_latency_ms: float | None = None,
) -> Selection | None:
    """SACE: cheapest admissible candidate.

    Admissibility requires ``quality >= min_quality``, ``risk <= max_risk``
    and, when given, ``latency_ms <= max_latency_ms``. Ties break on name so
    the result is deterministic. ``None`` means no candidate is admissible;
    the caller must keep the original representation.
    """
    floor = _probability("min_quality", min_quality)
    ceiling = _probability("max_risk", max_risk)
    latency_limit = (None if max_latency_ms is None
                     else _non_negative("max_latency_ms", max_latency_ms))
    admitted: list[Candidate] = []
    rejected: list[Candidate] = []
    for candidate in candidates:
        ok = candidate.quality >= floor and candidate.risk <= ceiling
        if latency_limit is not None:
            ok = ok and candidate.latency_ms <= latency_limit
        (admitted if ok else rejected).append(candidate)
    if not admitted:
        return None
    chosen = min(admitted, key=lambda item: (item.cost, item.name))
    return Selection(
        chosen=chosen,
        rejected=tuple(rejected + [item for item in admitted
                                   if item is not chosen]),
    )


_MIN_AXES = ("cost", "latency_ms", "overhead", "risk")
_MAX_AXES = ("quality", "savings", "reuse", "applicable")


def dominates(left: Candidate, right: Candidate) -> bool:
    """True when ``left`` is at least as good on every axis and better on one.

    Lower is better for cost, latency, overhead and risk; higher is better for
    quality, savings, reuse and applicability.
    """
    no_worse = all(getattr(left, axis) <= getattr(right, axis) for axis in _MIN_AXES)
    no_worse = no_worse and all(
        getattr(left, axis) >= getattr(right, axis) for axis in _MAX_AXES)
    strictly_better = any(
        getattr(left, axis) < getattr(right, axis) for axis in _MIN_AXES)
    strictly_better = strictly_better or any(
        getattr(left, axis) > getattr(right, axis) for axis in _MAX_AXES)
    return no_worse and strictly_better


def pareto_front(candidates: tuple[Candidate, ...] | list[Candidate]
                 ) -> tuple[Candidate, ...]:
    """Non-dominated candidates, in input order."""
    items = list(candidates)
    return tuple(
        item for item in items
        if not any(dominates(other, item) for other in items if other is not item)
    )
