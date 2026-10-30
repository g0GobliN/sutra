# Semantic retrieval: deferred

**Decision:** `sutra_restore` keeps its literal, bounded search. Semantic
retrieval is not implemented in this release.

This is a decision record, not a promise that the gap is unimportant. A literal
query such as `why did the test fail` will not find a line containing
`ValueError: boom`; that is the gap semantic retrieval would address. The
current design deliberately prefers exact, reproducible evidence over ranked
guesses.

Related: [the recovery protocol](recovery.md), [the correctness
contract](contract.md), [the security model](../SECURITY.md).

## What the current surface guarantees

| Property | Current implementation |
|---|---|
| Matching | Literal substring scan over lines; `case_sensitive` defaults to true |
| Query input | At most 200 characters; regex metacharacters have no special meaning |
| Result shape | Exact lines with headers, or an explicit clipped byte window |
| Work bounds | One entry ≤32 MiB, response ≤64 KiB, at most 20 matching lines |
| Locality | No network code; no model call; no third-party dependency |
| Determinism | Same entry and request produce the same response |
| Integrity | `mode=full` reports the store's SHA-256 check; byte offsets are exact |

The search implementation is in [`recovery.py`](../recovery.py). The package
declares `dependencies = []` in [`pyproject.toml`](../pyproject.toml), and the
product modules import no network client. The recovery envelope is documented
in [recovery.md](recovery.md).

## Why semantic retrieval is not a small change

A semantic mode would have to change what "recovered evidence" means. The
protocol currently returns content at exact byte or line addresses. A ranked
semantic hit returns an approximate snippet; the caller cannot tell whether the
ranked snippet is the evidence it asked for or merely nearby text. That is a
different contract, not another query option.

The candidate designs all carry a cost the current architecture does not pay:

1. **A local embedding index.** Requires model weights or a new runtime
   dependency, an index lifecycle, storage and cleanup, and a pinned model
   version for reproducibility. Tool output can contain secrets; an index would
   duplicate that exposure unless it is encrypted or held only in memory.
2. **Query expansion through a model.** Requires a provider call, credentials,
   variable latency and a new prompt-injection surface. The project explicitly
   makes no live-model claim and currently calls no model.
3. **Lexical proxies (stemming, synonyms, token overlap).** Local and cheaper,
   but still not semantic. It would add ambiguity and ranking behavior without
   evidence that the extra surface improves real task outcomes.

The existing envelope is already measurable: at the 32 MiB cap, a search under
load 22.36 on 4 CPUs had a 507 ms median for an absent token and multi-second
outliers; the artifact is marked not representative and the protocol
deliberately avoids a wall-clock promise (see [recovery.md § Execution-time
envelope](recovery.md#execution-time-envelope)). Adding an index build or model
call would consume that budget before any evidence is returned.

## Revisit conditions

Semantic retrieval can be reconsidered when all of these are true:

- an offline, pinned local model exists and the installation remains free of
  network calls;
- the index has explicit size, retention and cleanup rules, and its contents
  follow the store's permission model;
- the protocol labels results as approximate and still supplies exact
  byte/line addresses for the returned text;
- the mode is opt-in, never the default, and cannot displace the exact modes;
- a reproducible A/B task evaluation shows better task success without a
  regression in correctness, latency or privacy;
- adversarial tests cover prompt injection, malformed queries, oversized
  indexes and cleanup during an active restore.

Until then, the literal search remains the only search surface. If a caller
needs evidence that a literal query cannot find, the honest path is to re-run
the original tool with a narrower command, or to ask for a known line/byte
range. The marker already says which exact range it omitted.
