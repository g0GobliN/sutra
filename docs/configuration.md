# Configuration

Every setting is read on each call, so a change takes effect without restarting
the process. Defaults are a disk and context commitment, not just tuning knobs.

| Variable | Default | Meaning |
|---|---|---|
| `SUTRA_ENABLED` | `1` | `0` disables compression entirely, and retention with it |
| `SUTRA_MAX_BYTES` | `24576` | results at or below this are untouched; larger results are rewritten to fit |
| `SUTRA_HEAD_BYTES` | `12288` | bytes kept from the start |
| `SUTRA_TAIL_BYTES` | `6144` | bytes kept from the end (scaled down proportionally if head and tail exceed the cap) |
| `SUTRA_STATS_FILE` | `$HERMES_HOME/cache/sutra-stats.jsonl` | where savings are recorded |
| `SUTRA_STATS_MAX_BYTES` | `5242880` | rotate the log at this size (one `.1` file is kept) |
| `SUTRA_STORE` | `1` | `0` stops retaining originals; markers then say "not retained" |
| `SUTRA_STORE_DIR` | `$HERMES_HOME/cache/sutra-store` | where originals are retained |
| `SUTRA_STORE_TTL_HOURS` | `168` | age at which a retained original expires (7 days) |
| `SUTRA_STORE_MAX_BYTES` | `134217728` | total size of the store (128 MiB) |
| `SUTRA_STORE_MAX_ENTRY_BYTES` | `33554432` | largest single original retained (32 MiB) |
| `SUTRA_STORE_MAX_ENTRIES` | `2000` | most originals retained |
| `SUTRA_RECOVERY_MAX_BYTES` | `8192` | default response cap for `sutra_restore` (64 KiB hard maximum) |
| `SUTRA_RECOVERY_MAX_MATCHES` | `5` | default matching lines per search (20 hard maximum) |
| `SUTRA_POLICY` | `off` | `off`, `balanced` or `aggressive` agent guidance |
| `SUTRA_REUSE` | `1` | replace an exact repeat with a marker quoting the earlier id |
| `SUTRA_REUSE_MIN_BYTES` | `4096` | smallest output eligible for exact-duplicate reuse |
| `SUTRA_SEARCH` | `off` | `off`, `safe`, `balanced` or `aggressive` bounded post-compression optimization; `off` keeps the single-pass output byte-for-byte |
| `SUTRA_SEARCH_PASSES` | `4` | most accepted optimization passes per result (1-16) |
| `SUTRA_SEARCH_BUDGET_MS` | `50` | time budget for the optimization loop (1-5000 ms) |
| `SUTRA_LEDGER_FILE` | unset | optional JSONL path for scoped failure records; unset keeps the ledger in memory only |
| `SUTRA_LANGUAGE` | `0` | opt-in wording compression on prose lines while a search mode is active; never applied to code, quotes or fenced blocks |
| `SUTRA_MAX_TOKENS` | unset | advisory token ceiling per result; unset means no token budget |
| `SUTRA_TOKENIZER` | `byte-estimate` | `byte-estimate` or `tiktoken` (falls back when unavailable) |
| `SUTRA_TELEMETRY` | `0` | enable OpenTelemetry export; see [telemetry.md](telemetry.md) |
| `SUTRA_TELEMETRY_ENDPOINT` | `http://localhost:4318/v1/metrics` | OTLP HTTP metrics endpoint |
| `SUTRA_TELEMETRY_SAMPLE_RATE` | `1.0` | trace sampling ratio from `0.0` to `1.0`; metrics are not downsampled, and `0.0` disables both metric recording and spans |
| `SUTRA_JSON_ITEMS` | retired | ignored; a warning is logged if set |

## Store behaviour

Originals live under `$HERMES_HOME/cache/sutra-store/` by default, or under
`SUTRA_STORE_DIR` when set. The directory is created `0700`; files are written
atomically as `0600`; ids are 128-bit random values validated before any path is
built. Retention is bounded by the entry, total-size, count and age limits above.

The store is **not encrypted**. Tool output can contain secrets, and any process
running as the same user can read the store. Set `SUTRA_STORE=0` if that is not
acceptable: compression keeps working, but markers say the original was not
retained and quote no id.

## Recovery bounds

`sutra_restore` returns at most `SUTRA_RECOVERY_MAX_BYTES` per response (8 KiB by
default, 64 KiB hard maximum) and at most `SUTRA_RECOVERY_MAX_MATCHES` matching
lines per search (5 by default, 20 hard maximum). `mode="full"` refuses above the
response cap; explicit `bytes` and `lines` pages remain available. A single line
longer than the cap is returned clipped.

## Agent policy

`SUTRA_POLICY` is independent of compression and selects a bounded guidance
section that Hermes freezes into each new session prompt through its documented
`register_system_prompt_section` API. `off` is the default and returns an empty
section, which the host skips; `balanced` is recommended when the guidance is
enabled, and `aggressive` adds stricter preferences without permitting skipped
work. `SUTRA_ENABLED=0` disables it too.
See [agent-policy.md](agent-policy.md) for the API evidence and safeguards.

Policy text is SUTRA-authored and static. It never writes `config.yaml` or any
host prompt template, and a build without the section API simply gets
compression and recovery.

## Exact-duplicate reuse

`SUTRA_SEARCH` enables a bounded, opt-in second stage that tries cheap
formatting strategies (line endings, unicode spaces, trailing spaces, blank
runs, deep indentation) on text the engine already compressed. Every candidate
must pass the protected-span check (paths, URLs, numbers, identifiers,
commands, negations, error and security terms all survive) and must be
strictly smaller; at most `SUTRA_SEARCH_PASSES` passes run inside
`SUTRA_SEARCH_BUDGET_MS`. `off` is the default and changes nothing.
The modes differ in how much they are allowed to try, not in what they accept:

| Mode | Passes | Strategies | Combination search |
|---|---|---|---|
| `off` | none | none | no |
| `safe` | 1 | formatting only; never the language pass | no |
| `balanced` (also `on`) | 1 | formatting, plus language when `SUTRA_LANGUAGE=1` | no |
| `aggressive` (also `maximum`) | `SUTRA_SEARCH_PASSES` | formatting, plus language when `SUTRA_LANGUAGE=1` | yes, only after no single strategy is accepted |

Every mode applies a candidate only when it is strictly smaller and passes the
protected-span check against the text at the start of the run. The combination
search tries an ordered pair of strategies under the same validator and the
same time budget; a pair is accepted only if it saves at least
`min_saved_bytes` (one byte by default) and is the best-ranked pair.

`SUTRA_LANGUAGE=1` adds one more strategy, only while a search mode is active:
a fixed table of verbose phrases and politeness fillers is removed from prose
lines outside fenced blocks, quoted strings and marker lines. It is
deterministic, never paraphrases, and every candidate still passes the same
protected-span check, so negations, numbers, identifiers, commands, paths,
URLs, error and security terms survive or the candidate is rejected.

With `SUTRA_REUSE=1` (default), a result that exactly matches an earlier
compressed result in the same process session is replaced by a marker quoting
the earlier id. Only plain text rewritten by the `head-tail`, `dedupe` or `ansi`
rules is remembered; diffs, JSON documents and tracebacks are never replaced by
a reference. Before a marker is emitted, the store entry is checked for
existence, size, hash and TTL. The in-process index holds hashes and ids only
(not content), is bounded to 64 sessions × 128 digests, and resets on restart.
A hit is reported as rule `reuse` in the statistics log.

## Provider-side isolation

The store follows `HERMES_HOME` / `SUTRA_STORE_DIR` per profile. A Claude Code
hook and its MCP server must use the same store, or a marker written by one is
`not_found` in the other. Set `SUTRA_STORE_DIR` (or `HERMES_HOME`) in both
environments when you configure them separately.

The API form is `sutra.configuration()`, which returns every resolved value as a
frozen, inspectable object, including `policy_mode`, `reuse` and
`reuse_min_bytes`.

## Conceptual configuration, and the one resolver

goals.md PART 18 sketches a configuration grouped by concern. SUTRA does not
read that YAML and does not add a second configuration system: `config.py` is
the single resolver, and environment variables are its only input. The sketch
maps onto it like this, so the intent is readable without pretending a file
exists:

```yaml
mode: balanced            # SUTRA_SEARCH=off|safe|balanced|aggressive
optimization:
  compression: true       # SUTRA_ENABLED
  reuse: true             # SUTRA_REUSE, SUTRA_REUSE_MIN_BYTES
  language: false         # SUTRA_LANGUAGE
  search:
    maximum_effort: true  # SUTRA_SEARCH=aggressive
    passes: 4             # SUTRA_SEARCH_PASSES
    budget_ms: 250        # SUTRA_SEARCH_BUDGET_MS
safety:
  protected_spans: true   # always on; preserve.py has no off switch
  mandatory_context: true # context items marked mandatory are never dropped
  probability_cutoff: 0   # no probabilistic exclusion at any setting
cache:
  local_result: true      # SUTRA_STORE_ENABLED and the SUTRA_STORE_* bounds
  tool_result: explicit   # redundancy.py; a host must ask
  provider_prompt: unobservable
accounting:
  prices: caller-supplied # sutra cost flags; no price table ships
benchmarks:
  tokenizer: o200k_base   # bench/maximum.py; labelled estimated without tiktoken
```

Two rows deserve their own sentence. `probability_cutoff: 0` is not a default
that can be raised: no rule in this project drops content because it is
probably unimportant. And `protected_spans` is not a toggle — every candidate
must pass the protected-span check or it is not applied; there is no setting
that turns safety off.
