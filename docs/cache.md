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
| Tool result | library only (`redundancy.py`) | nothing automatically | call fingerprint plus dependency snapshot | changed dependency, expiry, mandatory command |
| Provider prompt | **not observable** | provider-side prefix cache | provider-specific prefix | provider policy |
| Semantic | **not implemented** | nothing | would need embeddings | n/a |

Local result cache detail: entries are originals plus a metadata file, capped by
count, total bytes and per-entry bytes, pruned by TTL, and not encrypted. A
lookup returns `ok`, `missing`, `expired`, `invalid` or `unavailable`; it never
returns a partial original. The full work is documented in
[recovery.md](recovery.md).

Context and tool-result layers are available to a host that asks (through
`sutra context` or `sutra redundancy`), but a PostToolUse-style hook sees
results, not calls or conversations, so nothing is cached automatically. That
is recorded here instead of being counted as a saving.

The provider prompt cache cannot be observed from a post-tool hook at all: SUTRA
cannot see whether a provider served cached input tokens, cannot shape the
prefix, and therefore never counts them as a SUTRA saving. The provider's own
usage metadata is the only source for that number, which is why
`sutra cost --optimized-cached N --cached-price P` asks for it explicitly.

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
