"""The one optimization orchestrator.

Before this module each engine had its own switch and its own policy:
``optimize.py`` ran formatting passes, ``language.py`` was appended only when
two separate environment variables agreed, ``redundancy.py`` decided reuse
separately, and the context and cache layers were reachable only from the CLI.
The four-mode design needs one owner that answers, for a given mode, *which
engines may run, in what order, with what budget, and why one candidate beat
another*.

This module is that owner. It does not re-implement any engine: it composes
the existing deterministic ones and records the decision in the vocabulary of
the optimization model in ``scoring.py`` and ``preserve.py``.

The four modes are resource and search-effort policies, never different
quality guarantees:

``off``
    No optimization engine runs: no formatting search, Caveman, Ponytail,
    context selection or cache advice. The base compression path (head/tail
    elision with stored recovery) is not an optional engine and keeps its
    existing behaviour, so an installation that sets nothing new sees the same
    bytes it saw before the four-mode system existed. Exact-duplicate reuse is
    off in this mode, because suppressing a result is an optimization.
``balanced``
    Formatting plus Caveman (prose wording), two passes, single candidates
    only. Ponytail may reuse only a call classified ``SAFE_TO_REUSE``; context
    selection and cache advice are available to hosts that ask for them.
``aggressive``
    Formatting plus Caveman, four passes, ordered pair combinations, and
    Ponytail may reuse a call classified ``SAFE_TO_REUSE`` or
    ``REQUIRES_REVALIDATION`` after its dependency snapshot matches. Context
    selection may drop optional items.
``beast``
    Eight passes, pair and triple combinations, the whole deterministic
    candidate space within the size cap. Still deterministic, still fails open.

SPC is enforced for every candidate by ``preserve.preservation_ok`` before a
candidate can be selected. SROF rejects a plan whose measured byte saving is
not positive after its recorded overhead. SEOF ranks. SACE selects the final
plan by lowest normalised cost among admissible plans. SMESP orders plan
evaluation so a plan with better expected value is explored first; when the
pass budget is exhausted, the unexplored plans are recorded as such rather
than silently discarded.

Every input to the decision functions is normalised and dimensionless:

* savings are ``bytes_saved / original_bytes`` (fraction of the payload);
* overhead is the added output bytes the action commits to (the recovery
  marker it must emit). Formatting and Caveman add no marker, so their
  overhead is 0 bytes. SUTRA's own processing time is a latency constraint
  measured in milliseconds, never converted into tokens: doing that without a
  price would invent a number;
* quality is 1.0 when the exact protected-span validator accepted the plan and
  0.0 otherwise (there is no estimated probability, by design);
* risk is 0.0 because there is no semantic model to estimate it; the honest
  statement is "unknown, bounded by exact validation".

Measured milliseconds and bytes are reported next to the decision, never
converted into tokens or currency without a price or tokenizer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import config, context, cost, language, optimize, scoring

__all__ = [
    "MODE_POLICIES",
    "ModePolicy",
    "Plan",
    "PlanResult",
    "Report",
    "policy_for",
    "engines",
    "optimize_text",
    "optimize_context",
    "decide_execution",
    "cache_advice",
]

# Fixed nominal search overhead for one plan, in "plan units". It is a
# deterministic stand-in for the measured elapsed time: two identical payloads
# must select the same plan on an idle and a loaded host. Real time is still
# measured and reported.
RANKING_OVERHEAD = 0.0
# The two ways to supply SROF's ``O(a)``. ``nominal`` keeps ranking independent
# of host load; ``measured`` charges each plan the share of the mode's
# processing budget it actually used, so a plan that spends the whole budget to
# save a sliver is refused. Both are dimensionless ratios, which is what makes
# them comparable with the saved fraction.
OVERHEAD_SOURCES = ("nominal", "measured")
# Sampling for SMESP ordering never changes the text; it only decides which
# plan is tried first when the budget cannot cover every plan.
SAMPLE_BYTES = 8 * 1024
# Above this the candidate space is not multiplied; the deterministic
# single-strategy passes still run. A size bound keeps the choice independent
# of host load, unlike a deadline.
COMBINATION_MAX_BYTES = 256 * 1024
TRIPLE_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class ModePolicy:
    """The resource envelope one mode grants to the engines."""

    mode: str
    passes: int
    combine: bool
    triple: bool
    language: bool
    plans: tuple[str, ...]
    min_saved_bytes: int = 1
    budget_ms: float = 50.0
    max_payload_bytes: int = COMBINATION_MAX_BYTES
    reuse: str = "off"  # off | safe | revalidate

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "passes": self.passes,
            "combine": self.combine,
            "triple": self.triple,
            "language": self.language,
            "plans": list(self.plans),
            "min_saved_bytes": self.min_saved_bytes,
            "budget_ms": self.budget_ms,
            "max_payload_bytes": self.max_payload_bytes,
            "reuse": self.reuse,
        }


MODE_POLICIES: dict[str, ModePolicy] = {
    "off": ModePolicy(mode="off", passes=0, combine=False, triple=False,
                      language=False, plans=(), budget_ms=0.0),
    "balanced": ModePolicy(mode="balanced", passes=2, combine=False,
                           triple=False, language=True,
                           plans=("format", "caveman"), budget_ms=50.0,
                           reuse="safe"),
    "aggressive": ModePolicy(mode="aggressive", passes=4, combine=True,
                             triple=False, language=True,
                             plans=("format", "caveman", "format+caveman"),
                             budget_ms=150.0, reuse="revalidate"),
    "beast": ModePolicy(mode="beast", passes=8, combine=True, triple=True,
                        language=True,
                        plans=("format", "caveman", "format+caveman"),
                        budget_ms=400.0, reuse="revalidate"),
}


def _legacy_policy(cfg: config.Config) -> ModePolicy:
    """The pre-``SUTRA_MODE`` behaviour, expressed as a policy.

    This is what keeps an installation that sets only ``SUTRA_SEARCH`` on
    byte-identical output: ``safe`` stays formatting-only, and
    ``SUTRA_LANGUAGE`` remains the only switch that adds Caveman. The mode name
    records the legacy source so reports do not claim an explicit mode ran.
    """
    search = cfg.search_mode
    if search == "off":
        return MODE_POLICIES["off"]
    if search == "safe":
        return ModePolicy(mode="legacy:safe", passes=1, combine=False,
                          triple=False, language=False, plans=("format",),
                          budget_ms=float(cfg.search_budget_ms))
    # A legacy run is one plan: the old code appended Caveman to the formatting
    # strategy list and let one optimizer pass mix them. Splitting it into
    # separate plans here would change the byte output existing users rely on.
    plans = ("format+caveman",) if cfg.language else ("format",)
    if search == "balanced":
        return ModePolicy(mode="legacy:balanced", passes=1, combine=False,
                          triple=False, language=cfg.language, plans=plans,
                          budget_ms=float(cfg.search_budget_ms))
    return ModePolicy(mode="legacy:aggressive", passes=cfg.search_passes,
                      combine=True, triple=False, language=cfg.language,
                      plans=plans, budget_ms=float(cfg.search_budget_ms))


def policy_for(cfg: config.Config) -> ModePolicy:
    """Return the policy the configuration selects.

    A legacy ``SUTRA_SEARCH`` value is only consulted when no explicit mode
    (environment or mode file) is set. ``SUTRA_MODE`` therefore wins, and the
    precedence is visible in ``cfg.mode_source``.
    """
    if cfg.mode_source == "legacy":
        return _legacy_policy(cfg)
    return MODE_POLICIES[cfg.mode]


@dataclass(frozen=True)
class Engine:
    """One engine the orchestrator may run, and the plane it acts on."""

    name: str
    plane: str
    modes: tuple[str, ...]
    automatic: bool
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "plane": self.plane,
            "modes": list(self.modes),
            "automatic": self.automatic,
            "note": self.note,
        }


_ENGINES: tuple[Engine, ...] = (
    Engine("format", "text", ("balanced", "aggressive", "beast"), True,
           "lossless formatting normalisation (CRLF, unicode spaces, "
           "trailing spaces, blank runs, deep indentation)"),
    Engine("caveman", "text", ("balanced", "aggressive", "beast"), True,
           "deterministic prose wording compression, validated by SPC"),
    Engine("ponytail", "execution", ("balanced", "aggressive", "beast"), False,
           "pre-call reuse decision; the host must call decide_execution "
           "before it executes the call"),
    Engine("context", "selection", ("balanced", "aggressive", "beast"), False,
           "value-per-token context selection; requires an item list from the host"),
    Engine("cache", "reuse", ("balanced", "aggressive", "beast"), False,
           "local/provider/semantic reuse advice; provider cache is not observable"),
)


def engines() -> tuple[Engine, ...]:
    """The engine registry, for reporting and tests."""
    return _ENGINES


def _format_strategies() -> list[optimize.Strategy]:
    return list(optimize.default_strategies())


def _caveman_strategy() -> optimize.Strategy:
    return optimize.Strategy(
        "language", language.compress_text,
        "remove filler, verbosity and duplicate prose; SPC-validated")


def _strategy_registry() -> dict[str, optimize.Strategy]:
    """One instance per built-in strategy, built once for one search.

    The plans of a search share these objects, which is what lets the search
    memo answer for the same object and input text a second time: the
    combination plan runs the strategies the formatting plan just ran. A
    registry per search also keeps the memo from ever conflating two distinct
    strategies that happen to share an apply function.
    """
    registry: dict[str, optimize.Strategy] = {
        item.name: item for item in optimize.default_strategies()}
    registry["language"] = _caveman_strategy()
    return registry


def _plan_strategies(plan: str,
                     registry: dict[str, optimize.Strategy]) -> list[optimize.Strategy]:
    strategies: list[optimize.Strategy] = []
    if "format" in plan:
        strategies.extend(registry[item.name] for item in optimize.default_strategies())
    if "caveman" in plan:
        strategies.append(registry["language"])
    return strategies


@dataclass(frozen=True)
class Plan:
    """One candidate optimization plan (a named strategy chain)."""

    name: str
    strategies: tuple[optimize.Strategy, ...]

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name,
                "strategies": [item.name for item in self.strategies]}


@dataclass(frozen=True)
class PlanResult:
    """Measured outcome of running one plan over the payload."""

    plan: str
    text: str
    saved_bytes: int
    original_bytes: int
    elapsed_ms: float
    applied: tuple[str, ...]
    accepted: int
    rejected: int
    error: str = ""

    @property
    def saved_fraction(self) -> float:
        if self.original_bytes <= 0:
            return 0.0
        return self.saved_bytes / self.original_bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan,
            "saved_bytes": self.saved_bytes,
            "saved_fraction": round(self.saved_fraction, 6),
            "elapsed_ms": round(self.elapsed_ms, 6),
            "applied": list(self.applied),
            "accepted": self.accepted,
            "rejected": self.rejected,
            "error": self.error,
        }


@dataclass(frozen=True)
class Report:
    """The orchestrator's result and the evidence for its choice."""

    text: str
    original_text: str
    mode: str
    policy: ModePolicy
    plans: tuple[PlanResult, ...]
    chosen: str
    saved_bytes: int
    elapsed_ms: float
    decision: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "policy": self.policy.as_dict(),
            "chosen": self.chosen,
            "saved_bytes": self.saved_bytes,
            "elapsed_ms": round(self.elapsed_ms, 6),
            "plans": [item.as_dict() for item in self.plans],
            "decision": self.decision,
        }


def _plans(policy: ModePolicy) -> tuple[Plan, ...]:
    plans: list[Plan] = []
    registry = _strategy_registry()
    for name in policy.plans:
        strategies = _plan_strategies(name, registry)
        if strategies:
            plans.append(Plan(name=name, strategies=tuple(strategies)))
    return tuple(plans)


def _sample_savings(text: str, plan: Plan) -> int:
    """Cheap deterministic estimate used only to order plan evaluation."""
    if plan.name == "caveman":
        sample = text[:SAMPLE_BYTES]
        try:
            return max(0, len(sample.encode("utf-8"))
                       - len(language.compress_text(sample).encode("utf-8")))
        except Exception:
            return 0
    return 0


def _ordered_plans(text: str, plans: tuple[Plan, ...],
                   budget_ms: float) -> tuple[Plan, ...]:
    """SMESP ordering: higher expected value first, deterministic tie-break.

    The estimate is the measured saving of the same deterministic strategy on
    a bounded sample, scaled by the sample's share of the payload. It is an
    estimate and is labelled as one; it never selects the output, only the
    order in which plans are evaluated.
    """
    total = max(1, len(text.encode("utf-8")))
    sample_size = max(1, min(SAMPLE_BYTES, total))
    sample_fraction = sample_size / total
    ordered: list[tuple[float, str, Plan]] = []
    for plan in plans:
        estimate = _sample_savings(text, plan)
        expected_fraction = (estimate / total) / sample_fraction
        overhead = RANKING_OVERHEAD / max(1.0, budget_ms / 50.0)
        value = scoring.priority(expected_fraction, overhead,
                                 applicable=1.0, reuse=1.0)
        ordered.append((value, plan.name, plan))
    ordered.sort(key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in ordered)


def _run_plan(text: str, plan: Plan, policy: ModePolicy,
              ledger: object | None,
              memo: optimize._Memo | None = None) -> PlanResult:
    """Run one plan. ``memo`` is shared by every plan of one search."""
    started = time.monotonic()
    try:
        report = optimize.optimize(
            text,
            max_passes=policy.passes,
            max_ms=policy.budget_ms,
            min_saved_bytes=policy.min_saved_bytes,
            strategies=plan.strategies,
            ledger=ledger,  # type: ignore[arg-type]
            combine=policy.combine,
            triple=policy.triple and plan.name != "caveman",
            memo=memo,
        )
    except Exception as exc:  # a broken plan is rejected, never fatal
        elapsed = (time.monotonic() - started) * 1000.0
        return PlanResult(
            plan=plan.name, text=text, saved_bytes=0,
            original_bytes=len(text.encode("utf-8")), elapsed_ms=elapsed,
            applied=(), accepted=0, rejected=0,
            error=f"{type(exc).__name__}: {exc}"[:200])
    accepted = sum(1 for item in report.evaluations if item.accepted)
    return PlanResult(
        plan=plan.name,
        text=report.text,
        saved_bytes=report.saved_bytes,
        original_bytes=len(text.encode("utf-8")),
        elapsed_ms=report.elapsed_ms,
        applied=report.applied,
        accepted=accepted,
        rejected=len(report.evaluations) - accepted,
    )


def search_overhead(result: PlanResult, policy: ModePolicy,
                    source: str = "nominal") -> float:
    """SROF's ``O(a)``: the cost of producing this plan, in plan units.

    ``nominal`` returns the documented constant, which keeps plan selection
    identical on an idle and a loaded host. ``measured`` charges the plan the
    fraction of the mode's processing budget it used: ``elapsed / budget``,
    capped at 1.0. Both are ratios, so they subtract cleanly from the saved
    fraction; nothing here converts bytes into milliseconds or money.
    """
    if source == "measured":
        budget = policy.budget_ms if policy.budget_ms > 0 else 1.0
        return min(1.0, max(0.0, result.elapsed_ms) / budget)
    return RANKING_OVERHEAD


def _decide(results: Sequence[PlanResult], policy: ModePolicy,
            original_bytes: int, *, overhead_source: str = "nominal",
            ) -> tuple[PlanResult | None, dict[str, object]]:
    """Run SROF, SEOF, SACE and Pareto over the measured plan results."""
    total = max(1, original_bytes)
    candidates: list[scoring.Candidate] = []
    trace: list[dict[str, object]] = []
    for result in results:
        if result.error:
            trace.append({"plan": result.plan, "admitted": False,
                          "reason": result.error})
            continue
        fraction = result.saved_bytes / total
        overhead = search_overhead(result, policy, overhead_source)
        # SROF: the measured benefit must survive the nominal search cost.
        benefit = scoring.net_benefit(fraction, overhead)
        admitted = benefit > 0 and result.saved_bytes > 0
        quality = 1.0 if not result.error else 0.0
        # SACE's latency constraint is the mode's pass bound, already enforced
        # by the loop. Measured wall-clock is reported, not admitted into the
        # decision: host load must not change which plan wins.
        plan_cost = 1.0 - fraction + overhead
        if not admitted:
            trace.append({
                "plan": result.plan,
                "admitted": False,
                "saved_bytes": result.saved_bytes,
                "overhead": round(overhead, 6),
                "overhead_source": overhead_source,
                "reason": "no positive net saving",
            })
            continue
        candidates.append(scoring.Candidate(
            name=result.plan, savings=fraction, overhead=overhead,
            quality=quality, risk=0.0, latency_ms=0.0,
            cost=max(0.0, plan_cost), applicable=1.0, reuse=1.0))
        trace.append({
            "plan": result.plan,
            "admitted": admitted,
            "saved_bytes": result.saved_bytes,
            "saved_fraction": round(fraction, 6),
            "sroF_benefit": round(benefit, 6),
            "overhead": round(overhead, 6),
            "overhead_source": overhead_source,
            "seof": round(scoring.seof(fraction, overhead,
                                       quality=quality, risk=0.0), 6),
            "sace_cost": round(max(0.0, plan_cost), 6),
        })
    if not candidates:
        return None, {"trace": trace, "sace": None,
                      "pareto": [], "reason": "no plan produced a result"}
    selection = scoring.sace_select(
        candidates, min_quality=1.0, max_risk=0.0)
    if selection is None:
        return None, {"trace": trace, "sace": None, "pareto": [],
                      "reason": "no admissible plan"}
    chosen = next((item for item in results
                   if item.plan == selection.chosen.name), None)
    if chosen is None:
        return None, {"trace": trace, "sace": None, "pareto": [],
                      "reason": "selected plan has no result"}
    front = scoring.pareto_front(candidates)
    return chosen, {
        "trace": trace,
        "sace": {"chosen": selection.chosen.name,
                 "cost": round(selection.chosen.cost, 6),
                 "rejected": [item.name for item in selection.rejected]},
        "pareto": [item.name for item in front],
        "seof_chosen": round(scoring.seof(
            selection.chosen.savings, selection.chosen.overhead,
            quality=selection.chosen.quality, risk=selection.chosen.risk), 6),
    }


def optimize_text(text: str, *, cfg: config.Config | None = None,
                  ledger: object | None = None,
                  structured: bool = False,
                  patch: bool = False) -> Report:
    """Run the configured mode over ``text`` and return the decision trace.

    ``structured`` (a JSON document) and ``patch`` (a diff) never reach an
    engine: the byte representation is the contract, so the original text is
    returned with a policy reason. The caller keeps its normal recovery path.
    """
    cfg = cfg or config.resolve()
    policy = policy_for(cfg)
    original_bytes = len(text.encode("utf-8", "surrogatepass"))
    empty = Report(text=text, original_text=text, mode=policy.mode, policy=policy, plans=(),
                   chosen="", saved_bytes=0, elapsed_ms=0.0,
                   decision={"reason": "not-run"})
    if not isinstance(text, str) or not text or original_bytes == 0:
        return empty
    if structured:
        return Report(text=text, original_text=text, mode=policy.mode, policy=policy, plans=(),
                      chosen="", saved_bytes=0, elapsed_ms=0.0,
                      decision={"reason": "structured payload: search skipped"})
    if patch:
        return Report(text=text, original_text=text, mode=policy.mode, policy=policy, plans=(),
                      chosen="", saved_bytes=0, elapsed_ms=0.0,
                      decision={"reason": "patch payload: search skipped"})
    if policy.passes <= 0 or not policy.plans:
        return Report(text=text, original_text=text, mode=policy.mode, policy=policy, plans=(),
                      chosen="", saved_bytes=0, elapsed_ms=0.0,
                      decision={"reason": "mode off"})
    if original_bytes > policy.max_payload_bytes:
        return Report(text=text, original_text=text, mode=policy.mode, policy=policy, plans=(),
                      chosen="", saved_bytes=0, elapsed_ms=0.0,
                      decision={"reason": "payload above search size bound"})
    started = time.monotonic()
    plans = _ordered_plans(text, _plans(policy), policy.budget_ms)
    # Every plan starts from the same text and the plans share strategies
    # (``format`` is a prefix of ``format+caveman``), so one memo across the
    # search computes each (strategy, input text) once. It changes no
    # candidate: a strategy is a pure function of its input.
    memo: optimize._Memo = {}
    results = [_run_plan(text, plan, policy, ledger, memo) for plan in plans]
    chosen, decision = _decide(results, policy, original_bytes,
                               overhead_source=getattr(
                                   cfg, "search_overhead", "nominal"))
    elapsed = (time.monotonic() - started) * 1000.0
    decision = dict(decision)
    decision["budget_ms"] = policy.budget_ms
    decision["plans_evaluated"] = [item.plan for item in results]
    if chosen is None:
        return Report(text=text, original_text=text, mode=policy.mode, policy=policy,
                      plans=tuple(results), chosen="", saved_bytes=0,
                      elapsed_ms=elapsed, decision=decision)
    return Report(text=chosen.text, original_text=text, mode=policy.mode, policy=policy,
                  plans=tuple(results), chosen=chosen.plan,
                  saved_bytes=max(0, original_bytes
                                  - len(chosen.text.encode("utf-8"))),
                  elapsed_ms=elapsed, decision=decision)


def optimize_context(items: Iterable[context.Item], budget: int, *,
                     cfg: config.Config | None = None,
                     weights: context.Weights | None = None,
                     unit: str = "tokens",
                     near_duplicates: bool = False) -> context.Selection | None:
    """Mode-gated context selection.

    Returns ``None`` when the mode is ``off``: the caller must keep the full
    context rather than receiving a silently truncated one. Selection itself
    is the existing value-per-token implementation; mandatory items are
    always kept by ``context.select`` and a budget conflict is reported.
    """
    cfg = cfg or config.resolve()
    policy = policy_for(cfg)
    if policy.passes <= 0:
        return None
    return context.select(list(items), budget, unit=unit, weights=weights,
                          near_duplicates=near_duplicates)


def decide_execution(call_fingerprint: str, *, ledger: object | None,
                     command: str = "",
                     dependencies: object | None = None,
                     cfg: config.Config | None = None) -> dict[str, object]:
    """Ponytail decision for one tool call, gated by the mode.

    Honest limit: this function can only act when the host asks *before* it
    executes. SUTRA's post-tool adapters cannot unsend a command they have
    already observed, so they use this only for calls a host routes through it.
    Mandatory commands (tests, lint, type checks, security scans) are never
    skipped by ``redundancy.Ledger.classify``.
    """
    from . import redundancy

    cfg = cfg or config.resolve()
    policy = policy_for(cfg)
    decision: dict[str, object] = {
        "action": "execute",
        "classification": redundancy.UNKNOWN,
        "reason": "mode off: no execution engine",
        "mode": policy.mode,
        "fingerprint": call_fingerprint,
    }
    if policy.passes <= 0 or ledger is None:
        return decision
    classify = getattr(ledger, "classify", None)
    if not callable(classify):
        decision["reason"] = "no redundancy ledger"
        return decision
    if dependencies is None:
        dependencies = redundancy.Snapshot()
    result = classify(call_fingerprint, dependencies=dependencies,
                      command=command)
    classification = getattr(result, "classification", result)
    decision["classification"] = str(classification)
    if classification == redundancy.SAFE_TO_REUSE:
        if policy.reuse in ("safe", "revalidate"):
            decision["action"] = "reuse"
            decision["reason"] = "dependencies unchanged and within TTL"
            return decision
    if classification == redundancy.REQUIRES_REVALIDATION:
        if policy.reuse == "revalidate":
            decision["action"] = "revalidate"
            decision["reason"] = "reuse allowed only after snapshot comparison"
            return decision
        decision["reason"] = "balanced mode requires SAFE_TO_REUSE"
        return decision
    if classification == redundancy.MUST_EXECUTE:
        decision["reason"] = "mandatory or unverifiable command"
        return decision
    decision["reason"] = "unknown classification"
    return decision


def cache_advice(economics: object, reuses: int, *,
                 cfg: config.Config | None = None) -> dict[str, object] | None:
    """Cache economics, mode-gated, without inventing provider observations.

    ``economics`` is a ``cache.Economics`` instance supplied by the caller.
    Returns ``None`` when no mode is active so a caller does not act on advice
    the user did not opt into.
    """
    cfg = cfg or config.resolve()
    policy = policy_for(cfg)
    if policy.passes <= 0:
        return None
    worth = getattr(economics, "worth_it", None)
    break_even = getattr(economics, "break_even", None)
    if not callable(worth) or not callable(break_even):
        return None
    return {
        "mode": policy.mode,
        "reuses": reuses,
        "worth_reusing": bool(worth(reuses)),
        "break_even_reuses": break_even(),
    }


def accounting(report: Report, *, cfg: config.Config | None = None,
               model_calls: int = 1,
               provider_latency_ms: float = 0.0) -> cost.Accounting:
    """Account one orchestrator run with the shared ``cost.Accounting`` type.

    Only input-side effects are known here: estimated original and billed input
    tokens plus SUTRA's own processing time. Post-generation compression does
    not change output tokens, so they are zero here and the caller adds the
    provider's real usage. Provider prompt-cache reads are not observable from
    this layer and are left at zero rather than guessed.
    """
    cfg = cfg or config.resolve()
    original_tokens = config.estimated_tokens(report.original_text,
                                              cfg.tokenizer)
    optimized_tokens = config.estimated_tokens(report.text, cfg.tokenizer)
    return cost.Accounting(
        original_input_tokens=original_tokens,
        billed_input_tokens=optimized_tokens,
        output_tokens=0,
        model_calls=max(1, model_calls),
        tool_calls=0,
        processing_ms=report.elapsed_ms,
        provider_latency_ms=provider_latency_ms,
        tokenizer=cfg.tokenizer,
        baseline_estimated=True,
    )
