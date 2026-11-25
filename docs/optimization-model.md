# The optimization model

SUTRA is allowed to compress only when it can show that the cheaper
representation is still correct. This document states the decision model that
enforces that rule, maps every formula to the code that implements it, and
separates what is measured from what is estimated.

The model has five parts: SPC, SROF, SEOF, SACE and SMESP. `preserve.py` implements the constraint, `scoring.py` implements the
four functions, and `optimize.py` calls them in the search loop. Nothing here
invents a price, a token count or a probability: callers supply those, or the
answer is reported as unavailable.

## SPC — the preservation constraint

An action \(a\) is admissible only when every mandatory constraint \(h_j\)
holds:

\[
h_j(a)=1 \quad \forall h_j\in\mathcal{H}
\]

The exact half of this constraint is `preserve.preservation_ok(original,
candidate)`. It extracts the spans a rewrite may not lose and compares their
counts:

| Kind | Examples | Rule |
|---|---|---|
| `code-fence` | fenced blocks | every block survives |
| `inline-code` | `` `value` `` | every span survives |
| `url` | `https://…` | every URL survives |
| `path` | `/etc/hosts`, `src/app.py` | every path survives |
| `command` | `rm -rf`, `pip install` | every command survives |
| `negation` | `do not`, `never` | every negation survives |
| `security` | tokens, keys, permissions | every term survives |
| `error` | `Error:`, `FAIL` | every term survives |
| `identifier` | `SUTRA_SEARCH`, `Node22` | every identifier survives |
| `number` | `3`, `0.5`, `6001` | total count must not fall |

Matching is whitespace-canonical, so re-indenting is allowed while dropping a
path is not. On an unclear case the validator answers "not preserved"; a
caller that cannot prove preservation keeps the original text. It never raises.

The semantic half of SPC is the confidence floor: `seof` takes a `quality` in
`[0, 1]`, and `sace_select` refuses a candidate below `min_quality`. SUTRA does
not claim a semantic-equivalence proof; it claims a conservative exact check
plus a caller-supplied confidence threshold.

## SROF — the resource objective

The expected resource benefit of an action is

\[
\Delta C(a)=\mathbb{E}[C_{\text{baseline}}-C_a], \qquad B(a)=\Delta C(a)-O(a)
\]

`scoring.net_benefit(savings, overhead)` returns \(B(a)\), and a candidate with
no demonstrated benefit is not applied. Inputs are numbers in one comparable
unit — bytes, tokens, milliseconds or currency, chosen by the caller. The
module performs no unit conversion: converting tokens to dollars needs a
tokenizer and a price, which `cost.py` accepts only as explicit inputs and
otherwise reports as `unavailable`.

When the orchestrator ranks plans it works in dimensionless ratios: the saving
is `saved_bytes / original_bytes`, and the overhead comes from
`SUTRA_SEARCH_OVERHEAD`.

| Value | \(O(a)\) | Behaviour |
|---|---|---|
| `nominal` (default) | the documented constant `RANKING_OVERHEAD` | two identical payloads select the same plan on an idle and a loaded host |
| `measured` | `elapsed_ms / budget_ms`, capped at 1.0 | a plan that spends its whole processing allowance to save a sliver is refused |

Both are ratios of two quantities SUTRA actually observes, so the subtraction
in \(B(a)\) stays unit-correct. The orchestrator records which one it used in
`decision["trace"][i]["overhead_source"]`, together with the number itself.

Both call sites pass ratios. `optimize.py` scores `saved_bytes / original_bytes`
against the same nominal constant; the orchestrator scores
`saved_bytes / original_bytes` against either constant or the measured share of
the mode's budget. In neither place is a byte count added to a clock reading.

## SEOF — the efficiency score

\[
\text{SEOF}(a)=
\frac{Q(a)\cdot[S_c(a)+S_t(a)+S_m(a)+S_k(a)]}{1+O(a)+R(a)}
\]

`scoring.seof(savings, overhead, quality=1.0, risk=0.0)` implements this with
`savings` standing for the applicable saving terms. The `1 +` makes the score
finite at zero overhead and zero risk.

The search loop ranks accepted candidates with

```python
scoring.seof(item.saved_bytes / size, optimize.RANKING_OVERHEAD)  # fixed 0.0
```

The saving and the overhead are both dimensionless. The overhead term is a
fixed nominal ratio, not the measured elapsed time. Ranking on real timings made the winner depend on host load: two
identical runs could pick two strategies of nearly equal size, and the
determinism test caught exactly that under parallel test load. Measured
`elapsed_ms` is still recorded on every evaluation and reported; it just does
not decide which candidate is applied. A future strategy that carries a real
confidence estimate or risk can pass it through the same `seof` call.

What is not claimed: the four saving terms are not separately observed for a
provider call. SUTRA cannot see cached tokens, tool-call counts or model calls
from inside a tool result. Those terms stay zero unless a host feeds them in,
which is why the benchmark reports "provider cost: unavailable".

## SACE — strategy selection under constraints

\[
a^*=\arg\min_{a\in A_{\text{safe}}}\mathbb{E}[C_{\text{total}}(a)]
\quad\text{s.t.}\quad
P(\text{success}\mid a)\ge Q_{\min},\;
P(\text{violation}\mid a)\le\epsilon,\;
L(a)\le L_{\max}
\]

`scoring.sace_select(candidates, min_quality=…, max_risk=…, max_latency_ms=…)`
returns the cheapest admissible candidate and the rejected ones, or `None`
when nothing is admissible. Ties break on name so the result is deterministic.
When reliable probabilities are unavailable the caller uses the conservative
defaults (`min_quality=0`, `max_risk=1`), which is the same behaviour as
"apply only what the exact validator accepted".

`orchestrator.py` is the production caller. It runs each mode plan as a
separate candidate (formatting, Caveman, or formatting followed by Caveman),
collects the admissible results, and calls `sace_select` to choose the final
one. `score=1.0` means the exact protected-span validator accepted the
candidate and `risk=0.0` means there is no semantic model, so the call is
`min_quality=1.0, max_risk=0.0`, not a probability cutoff.

## SMESP — the maximum-effort search policy

\[
\text{Priority}(a)=
\frac{P_{\text{applicable}}(a)\cdot\mathbb{E}[\Delta C(a)]\cdot V_{\text{reuse}}(a)}
{1+O_{\text{search}}(a)}
\]

`scoring.priority(savings, overhead, applicable=1.0, reuse=1.0)` is that
ranking signal. It is deliberately not a cutoff: a candidate with a
probability of `0.000001` still has a positive priority, and the loop's
stopping rule is the configured pass/time budget or the absence of an accepted
candidate — never a minimum probability or a minimum compression percentage.

Bound the effort, not the discovery: the mode policy caps what one call may
spend through its pass count and payload size, and `orchestrator` orders plans
with `scoring.priority` before evaluating them, so a larger mode explores more
plans instead of discarding low-probability ones. `SUTRA_SEARCH_BUDGET_MS` and
the policy budget are recorded, but neither stops the loop: a wall-clock stop
made the same input produce different output under host load, which the
determinism invariant caught. `SUTRA_MODE` selects the policy; `balanced` is the
installed default and `off` is the explicit opt-out.

## Pareto comparison

`scoring.pareto_front(candidates)` removes candidates dominated on cost,
quality, latency, reliability, savings, reuse and applicability (lower is
better for the first four, higher for the rest). `bench/maximum.py` uses it to
label the measured modes that are genuine trade-offs rather than dominated:
with o200k tokens on one axis and median milliseconds on the other, all three
modes can be non-dominated because each additional token saved costs time.

## The optimization loop, step by step

The loop below is what the code runs, in the order it runs it:

| Step | Where it happens |
|---|---|
| Observe request and workflow | the host calls `core.process_text` / `process_result`; SUTRA sees one payload, not the workflow |
| Select the mode policy | `orchestrator.policy_for(cfg)` maps `SUTRA_MODE` (or the legacy `SUTRA_SEARCH`) to plans, passes and reuse policy |
| Identify plausible opportunities | `engine._apply_search` runs after the deterministic head/tail pass and never for JSON or patches; `redundancy.classify` and `context.select` are the explicit entry points for call-level and context-level opportunities |
| Generate candidates | `orchestrator` runs the mode's plans; `optimize.optimize` applies formatting passes, ordered pairs when `combine=True`, and triples when `triple=True` and no single or pair was accepted |
| Rank candidates | `scoring.priority` orders plans before evaluation; `scoring.seof(fraction_saved, RANKING_OVERHEAD)` ranks candidates — never a probability cutoff and never host-load dependent |
| Test cheap candidates first | strategies are evaluated in fixed order inside one pass; the cheapest timings surface in `Evaluation.elapsed_ms` |
| Check preservation constraints | `preserve.preservation_ok` against the start-of-run text, for single candidates, pairs and triples alike |
| Reject and try next | a rejected candidate returns an `Evaluation(accepted=False, reason=…)` and changes nothing |
| Estimate net benefit | `scoring.net_benefit(fraction_saved, overhead)`; below `min_saved_bytes` is rejected |
| Apply validated optimization | `Report.applied` records the winning name; the text is replaced only after acceptance |
| Measure actual result | `Report.saved_bytes`, `Report.elapsed_ms`, per-candidate evaluations |
| Search again for remaining opportunities | the next pass re-evaluates every strategy against the new text; `aggressive` also tries ordered pairs and `beast` tries triples when nothing shorter was accepted |
| Final validation | the loop re-runs `preservation_ok` on the final text and `engine.compress` re-checks the JSON invariant before returning |
| Update adaptive policy | `failure.Ledger` disables a strategy for one payload shape after three consecutive hard failures and re-enables it on success; `SUTRA_MODE` selects the effort level. Learned policies are still deferred — see the limits below |
| Stop conditions | `max_passes` or no accepted candidate; no clock in the decision, so identical input gives identical output |

Inside the pair and triple phases each candidate is an ordered chain, and the
strategies are pure functions of their input, so the combination phase applies
every prefix once and reuses its text: three candidates cost three prefix
applications plus six suffixes instead of two applications per ordered pair.
The candidates, their order, their ranking and therefore the chosen text are
unchanged; only the repeated applications disappear. A 42 KB payload in `beast`
mode takes 56 ms instead of 128 ms on the same machine, with identical
evaluations and identical output.

The loop is fail-open: with `SUTRA_MODE=off` the diagram above does not execute
at all, and a broken strategy or validator can only lose its own candidate,
never the caller's text.

## Implementation map

| Component | Code | Live use |
|---|---|---|
| SPC (exact part) | `preserve.preservation_ok` | every accepted search candidate |
| SPC (confidence) | `scoring.seof(quality=…)`, `scoring.sace_select(min_quality=…)` | caller-supplied |
| SROF | `scoring.net_benefit` | `orchestrator` rejects a plan whose net benefit is not positive, using `SUTRA_SEARCH_OVERHEAD`; `optimize.optimize` rejects a non-positive candidate |
| SEOF | `scoring.seof` | `optimize.optimize` candidate ranking and the orchestrator's recorded score |
| SACE | `scoring.sace_select` | `orchestrator` final plan selection across admissible plans |
| SMESP | `scoring.priority` | `orchestrator` orders plans before evaluation; budget, not probability, stops the loop |
| Pareto | `scoring.pareto_front` | `orchestrator` reports the plan front; `bench/maximum.py` trade-off summary |

## Assumptions and limits

- Probabilities are supplied, not learned. SUTRA has no labelled corpus of
  successful and failed rewrites yet, so it uses deterministic defaults and
  records outcomes instead of pretending to estimate them.
- Units are not converted. A byte saving is not a token saving and a token
  saving is not a bill; `bench/tokens.py` counts tokens and `cost.py` needs an
  explicit price.
- The model ranks candidates; it does not prove semantic equivalence. Proof is
  the exact protected-span check plus keeping the original recoverable.
- Deterministic adaptation exists: the scoped failure ledger and the mode
  switch. Learned adaptation (bandits, estimated probabilities) is still
  deferred until enough reliable data exists to justify them, and that data
  does not exist yet.
