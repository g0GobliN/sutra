# Cache layers, metadata and economics

A cache can sit in five places between a tool result and a reader. This page
states which of them SUTRA owns, what each keys on, what invalidates it, and the
arithmetic a caller can use before trusting one. The distinction that matters most: a
**provider prompt cache** is not the **local result cache**, and money is only
saved by the first when the provider reports cached input tokens.

## The five layers

| Layer | Status | Stores | Keyed by | Invalidated by |
|---|---|---|---|---|
| Local result | **implemented** (`store.py`, `reuse.py`) | originals keyed by SHA-256 | content hash | TTL, size caps, content mismatch, delete |
| Context | library only (`context.py`) | nothing automatically | item ids and hashes | dependency snapshot |
| Tool result | **implemented where a pre-dispatch frame exists** (`replay.py`) | read-only tool results, served by the Hermes `tool_execution` middleware | call fingerprint plus a hash/mtime/size stamp of every file named | changed dependency, expiry, session end, size caps |
| Provider prompt | **observed, not controlled** (`providers.py`) | nothing of SUTRA's — the provider keeps the prefix | provider-specific prefix | provider policy |
| Semantic | **not implemented** | nothing | would need embeddings | n/a |

Local result cache detail: entries are originals plus a metadata file, capped by
count, total bytes and per-entry bytes, pruned by TTL, and not encrypted. A
lookup returns `ok`, `missing`, `expired`, `invalid` or `unavailable`; it never
returns a partial original. The full work is documented in
[recovery.md](recovery.md).

The context layer is available to a host that asks (`sutra context`), but no
shipped adapter hands SUTRA the conversation before the prompt is assembled, so
nothing is selected automatically — see
[integrations.md](integrations.md#which-lifecycle-stage-each-adapter-reaches).

The tool-result layer does run automatically, but only where the host exposes a
frame that sees a call before it executes. Hermes is the only one: its
`tool_execution` middleware consults the replay store first and serves the
recorded result when the call is a read-only tool naming regular files whose
hash, mtime and size still match, in the same session, within the TTL. Writes,
commands, tests, lint, type checks, security scans, directory listings and
remote fetches are never served, in any mode, because a directory stamp does not
cover the files inside it and side effects are unobservable. Every other adapter
sees the result after the fact and can only compress it.

The provider prompt cache is observable but not controllable. SUTRA never
decides what a provider caches, so it never counts a provider cache hit as a
SUTRA saving. What it does read is the provider's own usage payload: OpenAI's
`cached_tokens`, Anthropic's `cache_read_input_tokens` and
`cache_creation_input_tokens`, Google's `cachedContentTokenCount`, plus the
OpenAI-compatible `prompt_tokens_details.cached_tokens` shape. Those numbers are
priced by `sutra cost --usage-file` and labelled as provider-reported, never as
estimated. A payload without them leaves the field unavailable rather than
zero.

## Entry metadata

`cache.Metadata` carries what an entry needs to be invalidatable:

| Field | Why it is there |
|---|---|
| `input_fingerprint` | identifies the input the entry was built from |
| `provider`, `model` | an answer must never cross providers or models |
| `tool`, `tool_version` | a tool's output format changes between versions |
| `config_version` | a configuration change invalidates derived results |
| `dependencies` (name → stamp) | file and version stamps the result depends on |
| `created`, `expires` | expiry is explicit; `None` means no expiry |
| `validation` | `valid`, `expired`, `invalidated` or `unverified` |

`usable()` is `validation == "valid"` and not expired. `cache_key()` digests all
of the identity fields plus the sorted dependency map, so changing any one of
them produces a different key. Round-trip JSON is provided for storing the
metadata beside an entry.

## Economics

A cache layer is only worth writing when, over `N` reuses:

```
C_write + N * C_read < N * C_miss
N > C_write / (C_miss - C_read)          (when C_miss > C_read)
```

`sutra cache` evaluates that with your own unit — dollars, tokens, seconds —
and never assumes a price:

```console
$ sutra cache --write-cost 10 --read-cost 1 --miss-cost 5 --reuses 3
...
economics: C_write + N*C_read < N*C_miss
  saving per reuse: 4.0
  break-even reuses: 2.5
  net at 3 reuses: 2.0 (worth it: True)
```

If a hit costs at least as much as a miss, `break_even()` returns `None` and
the verdict is `False` for every reuse count: a cache that cannot win is
reported as such rather than being given a flattering number.

## Rules encoded here

- A cache key includes provider, model, tool, tool version, config version and
  dependencies, so no entry can leak across a version or provider change.
- There is no cross-user sharing: metadata has no user identity, and the local
  store is per user.
- Validation state is explicit; an `unverified` entry is not usable.
- Nothing in this module calls a provider or measures a token. It is
  arithmetic and metadata; the measurements live in
  [benchmark.md](benchmark.md) and [phase2-hermes-cost-report.md](phase2-hermes-cost-report.md).

## Why there is no semantic cache

The fifth layer is deliberately absent, and the reason is not effort. A semantic
cache would serve a stored result because a new request *looks* like the old one.
Similarity is not equivalence: two requests can embed close together and still
differ in a way that changes the answer — a different branch, a different file,
a different flag, one changed word in an instruction. Serving the old answer
then produces a confidently wrong result that no downstream check catches,
because the result arrives looking exactly like a real one.

For a reuse decision to be safe, the following would all have to be true, and
none of them is available today:

| Requirement | Why it is unmet |
|---|---|
| An exactness check after similarity | Needs to prove the two requests are *equivalent*, not close. No local model does that, and a threshold is a probability, not a proof. |
| Dependency equality | The stored entry's file/version/config stamps must still match. That part exists (`cache.Metadata.dependencies`), but it answers "unchanged?", not "same question?". |
| Freshness and session isolation | A near-duplicate match across sessions would leak one user's tool output into another's answer. The local store has no cross-session sharing, and a semantic index would have to prove it does not either. |
| Invalidation on dependency drift | A semantic index cannot invalidate precisely: it stores vectors, not the stamps that would show a file changed. |
| Economics | Without a hit rate, `C_w + N·C_r < N·C_n` cannot be evaluated. An index build also costs on every write, not only on reads. |
| No side effects | A cached *action* is never acceptable. Only read-only results could ever qualify — the same rule `replay.py` already applies. |

What the project does instead, in the order a caller would meet it:

1. `replay.py` reuses a result only when the request is *identical* (canonical
   arguments, session, tool) and every named file's hash, mtime and size still
   match; when uncertain it executes.
2. `redundancy.py` classifies a repeated call so a host can see it; it never
   fabricates a result from a nearby one.
3. `context.py` detects near-duplicate *items* with SimHash/MinHash, and that
   detection only ever **reports** overlap — it never removes an item on the
   strength of a similarity score.

Revisit conditions, all required: an offline, pinned local embedder; exact
verification of the candidate pair after retrieval (not a similarity threshold);
per-entry dependency stamps stored beside the vector; session- and
user-scoped indexes with the same isolation the local store has; measured hit
rate and cost; a labelled "approximate" result shape; and an A/B task evaluation
showing better outcomes with no correctness regression. Until then a repeated
question executes again, which is slower and always right.
