"""Cost accounting that never invents a bill.

SUTRA can measure bytes; it cannot see what a provider charges. The provider
returns usage metadata only to the host, and prices are a per-account fact.
Any dollar figure this project prints must therefore be built from inputs the
caller explicitly supplies, and the absence of an input must be visible in the
result rather than silently replaced by a default rate.

Rules:

1. **No built-in prices.** There is no price table in this module and no
   fallback rate. ``Pricing`` is constructed by the caller.
2. **Missing input means ``None`` plus a reason.** A cost that cannot be
   computed is ``None``, with ``cost.basis`` naming the missing piece. A zero
   baseline has no percentage, not 0%.
3. **Validate loudly at construction.** Negative counts, a cached count larger
   than the input count, and negative/non-finite rates raise ``ValueError``:
   bad accounting input is a caller bug, not a value to clamp.
4. **A plain dict is the report.** ``savings_report`` returns JSON-serialisable
   data with fixed keys, so a benchmark can assert on it and a document can
   quote it without reformatting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_ROUND = 12
_PERCENT_ROUND = 6


@dataclass(frozen=True)
class Usage:
    """Token counts as reported by, or estimated for, one provider call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError(
                "cached_input_tokens cannot exceed input_tokens: "
                f"{self.cached_input_tokens} > {self.input_tokens}"
            )

    def total_tokens(self) -> int:
        """Billable tokens: cached input is part of input, not extra."""
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }


@dataclass(frozen=True)
class Pricing:
    """Caller-supplied rates in currency units per million tokens."""

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None

    def __post_init__(self) -> None:
        for name in ("input_per_million", "output_per_million",
                     "cached_input_per_million"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if value < 0:
                raise ValueError(f"{name} must not be negative")

    def as_dict(self) -> dict[str, float | None]:
        return {
            "input_per_million": self.input_per_million,
            "output_per_million": self.output_per_million,
            "cached_input_per_million": self.cached_input_per_million,
        }


def price(usage: Usage, pricing: Pricing | None) -> float | None:
    """Cost of one ``Usage`` under explicit ``pricing``, or None.

    Cached input without its own rate is billed at the input rate; that is an
    explicit assumption, reported by ``savings_report`` as
    ``cached_input_rate == "assumed at input rate"``.
    """
    if pricing is None:
        return None
    cached_rate = (
        pricing.cached_input_per_million
        if pricing.cached_input_per_million is not None
        else pricing.input_per_million
    )
    uncached = usage.input_tokens - usage.cached_input_tokens
    total = (
        uncached * pricing.input_per_million
        + usage.cached_input_tokens * cached_rate
        + usage.output_tokens * pricing.output_per_million
    ) / 1_000_000
    return round(total, _ROUND)


def token_savings(baseline: Usage, optimized: Usage) -> int:
    """Tokens saved; negative means the optimized path used more."""
    return baseline.total_tokens() - optimized.total_tokens()


def percent_saved(baseline: Usage, optimized: Usage) -> float | None:
    """Percentage of baseline tokens saved, or None when baseline is empty."""
    total = baseline.total_tokens()
    if total <= 0:
        return None
    return round((total - optimized.total_tokens()) / total * 100.0, _PERCENT_ROUND)


def savings_report(
    *,
    baseline: Usage,
    optimized: Usage,
    pricing: Pricing | None = None,
    baseline_bytes: int | None = None,
    optimized_bytes: int | None = None,
    baseline_ms: float | None = None,
    optimized_ms: float | None = None,
) -> dict[str, object]:
    """Compare a baseline and an optimized run with explicit accounting.

    Every optional field that was not supplied stays ``None``; nothing is
    inferred or estimated. See the module docstring for the honesty rule.
    """
    baseline_usd = price(baseline, pricing)
    optimized_usd = price(optimized, pricing)
    saved_usd = (
        round(baseline_usd - optimized_usd, _ROUND)
        if baseline_usd is not None and optimized_usd is not None
        else None
    )
    if pricing is None:
        basis = "unavailable: no pricing supplied"
        cached_basis = "not applicable"
    elif saved_usd is None:
        basis = "unavailable: incomplete pricing"
        cached_basis = "assumed at input rate"
    else:
        basis = "explicit pricing"
        cached_basis = (
            "explicit"
            if pricing.cached_input_per_million is not None
            else "assumed at input rate"
        )
    saved_tokens = token_savings(baseline, optimized)
    percent = percent_saved(baseline, optimized)
    return {
        "baseline": {
            "tokens": baseline.total_tokens(),
            "bytes": baseline_bytes,
            "ms": baseline_ms,
        },
        "optimized": {
            "tokens": optimized.total_tokens(),
            "bytes": optimized_bytes,
            "ms": optimized_ms,
        },
        "saved_tokens": saved_tokens,
        "percent_saved": percent,
        "percent_saved_reason": None if percent is not None
        else "baseline has no tokens",
        "saved_bytes": (
            baseline_bytes - optimized_bytes
            if baseline_bytes is not None and optimized_bytes is not None
            else None
        ),
        "overhead_ms": (
            round(optimized_ms - baseline_ms, _ROUND)
            if baseline_ms is not None and optimized_ms is not None
            else None
        ),
        "pricing": pricing.as_dict() if pricing is not None else None,
        "cost": {
            "baseline_usd": baseline_usd,
            "optimized_usd": optimized_usd,
            "saved_usd": saved_usd,
            "basis": basis,
            "cached_input_rate": cached_basis,
        },
    }
