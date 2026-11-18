# The optimization model

SUTRA is allowed to compress only when it can show that the cheaper
representation is still correct. This document states the decision model that
enforces that rule, maps every formula to the code that implements it, and
separates what is measured from what is estimated.

The model has five parts, named in `goals.md` (PART 4): SPC, SROF, SEOF, SACE
and SMESP. `preserve.py` implements the constraint, `scoring.py` implements the
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

In the search loop, `savings` is the measured byte reduction and `overhead` is
the measured elapsed time of the candidate. That is the cheapest honest
measurement available without a provider call.

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
scoring.seof(item.saved_bytes, optimize.RANKING_OVERHEAD_MS)   # fixed 1.0
```

The overhead term in ranking is a fixed nominal millisecond, not the measured
elapsed time. Ranking on real timings made the winner depend on host load: two
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

The search loop currently evaluates one candidate per strategy, so SACE is the
selection rule for callers that hold several strategies at once; it is tested
directly in `tests/test_scoring.py`. It is not a hidden second optimizer.

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

Bound the effort, not the discovery: `SUTRA_SEARCH_PASSES` caps what one call
may spend, so the loop cannot cost more than the output it is optimizing, and
the pass cap is deterministic. `SUTRA_SEARCH_BUDGET_MS` is retained for
compatibility and validated, but it no longer stops the loop: a wall-clock stop
made the same input produce different output under host load, which the
determinism invariant caught. `off` remains the default.

## Pareto comparison

`scoring.pareto_front(candidates)` removes candidates dominated on cost,
quality, latency, reliability, savings, reuse and applicability (lower is
better for the first four, higher for the rest). `bench/maximum.py` uses it to
label the measured modes that are genuine trade-offs rather than dominated:
with o200k tokens on one axis and median milliseconds on the other, all three
modes can be non-dominated because each additional token saved costs time.

## The PART 5 loop, step by step

goals.md PART 5 draws the loop as a diagram. This is where each step lives, so
the diagram is a description of the code rather than an aspiration:

| PART 5 step | Where it happens |
|---|---|
| Observe request and workflow | the host calls `core.process_text` / `process_result`; SUTRA sees one payload, not the workflow |
| Identify plausible opportunities | `engine._apply_search` runs only for payloads the deterministic pass left alone; `redundancy.classify` and `context.select` are the explicit entry points for call-level and context-level opportunities |
| Generate candidates | `optimize.STRATEGIES` (formatting, wording) plus ordered pairs when `combine=True` |
| Rank candidates | `scoring.seof(saved_bytes, RANKING_OVERHEAD_MS)` — savings over a fixed nominal cost, never a probability cutoff and never host-load dependent |
| Test cheap candidates first | strategies are evaluated in fixed order inside one pass; the cheapest timings surface in `Evaluation.elapsed_ms` |
| Check preservation constraints | `preserve.preservation_ok` against the start-of-run text, for single candidates and pairs alike |
| Reject and try next | a rejected candidate returns an `Evaluation(accepted=False, reason=…)` and changes nothing |
| Estimate net benefit | `scoring.net_benefit(saved, overhead)`; below `min_saved_bytes` is rejected |
| Apply validated optimization | `Report.applied` records the winning name; the text is replaced only after acceptance |
| Measure actual result | `Report.saved_bytes`, `Report.elapsed_ms`, per-candidate evaluations |
| Search again for remaining opportunities | the next pass re-evaluates every strategy against the new text (and, in aggressive mode, pairs) |
| Final validation | the loop re-runs `preservation_ok` on the final text and `engine.compress` re-checks the JSON invariant before returning |
| Update adaptive policy | `failure.Ledger` disables a strategy for one payload shape after three consecutive hard failures and re-enables it on success; `SUTRA_SEARCH` selects the effort level. Learned policies are still deferred — see the limits below |
| Stop conditions | `max_passes` or no accepted candidate; no clock in the decision, so identical input gives identical output |

The loop is opt-in and fail-open: with `SUTRA_SEARCH=off` the diagram above does
not execute at all, and a broken strategy or validator can only lose its own
candidate, never the caller's text.

## Implementation map

| Component | Code | Live use |
|---|---|---|
| SPC (exact part) | `preserve.preservation_ok` | every accepted search candidate |
| SPC (confidence) | `scoring.seof(quality=…)`, `scoring.sace_select(min_quality=…)` | caller-supplied |
| SROF | `scoring.net_benefit` | tested; available to callers |
| SEOF | `scoring.seof` | `optimize.optimize` candidate ranking |
| SACE | `scoring.sace_select` | tested selection rule for multi-candidate callers |
| SMESP | `scoring.priority` | tested ranking signal; budget, not probability, stops the loop |
| Pareto | `scoring.pareto_front` | `bench/maximum.py` trade-off summary |

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
  deferred — `goals.md` says not to add machine learning until enough
  reliable data exists, and that data does not exist yet.
