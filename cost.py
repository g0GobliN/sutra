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


def _number(value: float | int | None, name: str, *, integer: bool = False) -> None:
    if value is None:
        return
    if integer:
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int")
        return
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0:
        raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True)
class Accounting:
    """The full per-session accounting set required by goals.md PART 14.

    Every field is either measured or explicitly marked estimated; nothing is
    derived from an assumed price. ``billed_input_tokens`` is what the provider
    charged for, which can differ from what SUTRA produced when the provider
    applies its own prompt cache.
    """

    original_input_tokens: int = 0
    billed_input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    processing_ms: float = 0.0
    validation_ms: float = 0.0
    provider_latency_ms: float | None = None
    total_latency_ms: float | None = None
    extra_cost: float = 0.0
    tokenizer: str = ""
    baseline_estimated: bool = False
    useful_tasks: int | None = None

    def __post_init__(self) -> None:
        for name in ("original_input_tokens", "billed_input_tokens",
                     "cached_input_tokens", "output_tokens", "model_calls",
                     "tool_calls"):
            _number(getattr(self, name), name, integer=True)
        for name in ("processing_ms", "validation_ms", "extra_cost"):
            _number(getattr(self, name), name)
        for name in ("provider_latency_ms", "total_latency_ms", "useful_tasks"):
            value = getattr(self, name)
            if value is not None:
                _number(value, name, integer=name == "useful_tasks")
        if self.cached_input_tokens > self.billed_input_tokens:
            raise ValueError(
                "cached_input_tokens cannot exceed billed_input_tokens: "
                f"{self.cached_input_tokens} > {self.billed_input_tokens}")

    def usage(self) -> Usage:
        """The billed usage of the optimized run."""
        return Usage(self.billed_input_tokens, self.output_tokens,
                     self.cached_input_tokens)

    def baseline(self) -> Usage:
        """The counterfactual: original input, same output, no cache."""
        return Usage(self.original_input_tokens, self.output_tokens, 0)

    def actual_cost(self, pricing: Pricing | None) -> float | None:
        """Provider cost for the optimized run plus SUTRA's own overhead."""
        base = price(self.usage(), pricing)
        return None if base is None else round(base + self.extra_cost, _ROUND)

    def baseline_cost(self, pricing: Pricing | None) -> float | None:
        return price(self.baseline(), pricing)

    def net_savings(self, pricing: Pricing | None) -> float | None:
        """Baseline cost minus actual cost (overhead included), or None."""
        baseline = self.baseline_cost(pricing)
        actual = self.actual_cost(pricing)
        if baseline is None or actual is None:
            return None
        return round(baseline - actual, _ROUND)

    def efficiency(self, pricing: Pricing | None) -> float | None:
        """Quality-adjusted efficiency E = useful tasks / actual cost."""
        if self.useful_tasks is None:
            return None
        actual = self.actual_cost(pricing)
        if actual is None or actual <= 0:
            return None
        return round(self.useful_tasks / actual, _ROUND)

    def overhead_ms(self) -> float:
        """SUTRA's own processing and validation time."""
        return round(self.processing_ms + self.validation_ms, _ROUND)

    def as_dict(self) -> dict[str, object]:
        return {
            "original_input_tokens": self.original_input_tokens,
            "billed_input_tokens": self.billed_input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "processing_ms": self.processing_ms,
            "validation_ms": self.validation_ms,
            "overhead_ms": self.overhead_ms(),
            "provider_latency_ms": self.provider_latency_ms,
            "total_latency_ms": self.total_latency_ms,
            "extra_cost": self.extra_cost,
            "tokenizer": self.tokenizer,
            "baseline_estimated": self.baseline_estimated,
            "useful_tasks": self.useful_tasks,
        }


def accounting_report(accounting: Accounting,
                      pricing: Pricing | None = None) -> dict[str, object]:
    """One JSON-ready report with every PART 14 field and its basis."""
    baseline_cost = accounting.baseline_cost(pricing)
    actual_cost = accounting.actual_cost(pricing)
    net = accounting.net_savings(pricing)
    original_total = accounting.original_input_tokens + accounting.output_tokens
    optimized_total = accounting.billed_input_tokens + accounting.output_tokens
    percent = (
        round((original_total - optimized_total) / original_total * 100.0,
              _PERCENT_ROUND)
        if original_total > 0 else None
    )
    if pricing is None:
        basis = "unavailable: no pricing supplied"
    elif net is None:
        basis = "unavailable: incomplete pricing"
    else:
        basis = "explicit pricing"
    return {
        "tokens": {
            "original_input": accounting.original_input_tokens,
            "billed_input": accounting.billed_input_tokens,
            "cached_input": accounting.cached_input_tokens,
            "output": accounting.output_tokens,
            "original_total": original_total,
            "optimized_total": optimized_total,
            "saved": original_total - optimized_total,
            "percent_saved": percent,
            "tokenizer": accounting.tokenizer,
            "reduction_basis": (
                "same tokenizer; comparable" if accounting.tokenizer
                else "tokenizer not named; do not compare across models"),
        },
        "calls": {
            "models": accounting.model_calls,
            "tools": accounting.tool_calls,
        },
        "latency": {
            "processing_ms": accounting.processing_ms,
            "validation_ms": accounting.validation_ms,
            "overhead_ms": accounting.overhead_ms(),
            "provider_ms": accounting.provider_latency_ms,
            "total_ms": accounting.total_latency_ms,
        },
        "cost": {
            "baseline": baseline_cost,
            "actual": actual_cost,
            "net_savings": net,
            "extra_cost": accounting.extra_cost,
            "basis": basis,
            "estimated_baseline": accounting.baseline_estimated,
        },
        "quality": {
            "useful_tasks": accounting.useful_tasks,
            "efficiency": accounting.efficiency(pricing),
            "efficiency_formula": "E = useful tasks / actual cost",
        },
        "accounting": accounting.as_dict(),
    }
