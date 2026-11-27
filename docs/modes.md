# Optimization modes

SUTRA exposes four modes: `off`, `balanced`, `aggressive` and `beast`. A mode is a
resource and search-effort policy. It is not a different compression guarantee,
and it does not change the protected-span rules: every candidate in every mode
must survive the same exact preservation check before it can replace a byte.

`balanced` is the installed default: an installation that sets nothing still
gets formatting, wording and safe reuse. `off` is the explicit opt-out that
keeps the base compression and recovery path without running an optimization
plan. Existing
installations remain compatible: the legacy `SUTRA_SEARCH` and `SUTRA_LANGUAGE`
variables are still honored when `SUTRA_MODE` is unset, and public APIs,
markers and adapters are unchanged.

## Set a mode

```bash
sutra mode                 # show the current mode, its source and its policy
sutra mode balanced        # write the mode file (~/.sutra/mode)
sutra mode off --clear     # remove the mode file
sutra mode aggressive --json
```

The mode file lives at `~/.sutra/mode`, or at `SUTRA_MODE_FILE` when set. Writing
it is atomic and validates the name first. It is read on every call.

Resolution order, highest first:

1. `SUTRA_MODE` in the environment;
2. the mode file;
3. the legacy `SUTRA_SEARCH` value (see [Migration](#migration-from-sutra_search));
4. `balanced`.

An unknown `SUTRA_MODE` logs a warning and falls through to the next source
instead of silently forcing a mode. An unknown value passed to `sutra mode` is
rejected and nothing is written.

## What each mode enables

All active modes keep the base compression path: a result larger than
`SUTRA_MAX_BYTES` is rewritten to a head and tail with a recovery marker. That
path is not an optimization engine and is not disabled by `off`.

| Mode | Optimization plans | Passes | Reuse policy | Approx. search bound |
|---|---|---:|---|---:|
| `off` | none | 0 | off | none |
| `balanced` | formatting, Caveman | 2 | `SAFE_TO_REUSE` only | 50 ms nominal |
| `aggressive` | formatting, Caveman, formatting+Caveman | 4, pair combinations | `SAFE_TO_REUSE` and `REQUIRES_REVALIDATION` | 150 ms nominal |
| `beast` | same as aggressive | 8, pair and triple combinations | same as aggressive | 400 ms nominal |

- **Formatting** is the deterministic set already described in
  [configuration.md](configuration.md): line endings, unicode spaces, trailing
  spaces, blank runs and deep indentation.
- **Caveman** removes filler and repeated prose from ordinary sentences with
  three deterministic passes: a verbose-phrase table (`in order to` → `to`), a
  subordination compaction that keeps the conjunction and drops the relative
  clause (`X, which means that Y` → `X, meaning Y`), and a line-opening reminder
  removal (`Note that X` → `X`). It keeps the sentence's grammatical skeleton —
  no paraphrase, no synonym selection, no clause reordering — and it skips code,
  fenced blocks, commands, quoted material and any line carrying shell or
  template syntax.
- **Ponytail** runs where a host asks before dispatching. `redundancy.py`
  classifies a repeated call as `SAFE_TO_REUSE`, `REQUIRES_REVALIDATION`,
  `MUST_EXECUTE` or `UNKNOWN` from dependency snapshots, and `replay.py` serves
  the recorded result in the Hermes `tool_execution` middleware, which is the
  one shipped frame that sees a call before it runs. Reuse is limited to
  structured read-only tools naming a regular file in the same session, with
  the dependency stamp still matching. Tests, lint, type checks, security scans,
  writes, commands, directory listings and remote fetches are never reused, in
  any mode. A host with a pre-execution stage asks `sutra pre-execute` about
  the call: the answer is `allow`, or `deny` quoting the recovery id of the
  result that identical call already produced, which the agent exchanges for
  the exact bytes with `sutra_restore`. The index is keyed on the tool name and
  the canonical JSON of its arguments inside one session, keeps a bounded
  number of calls, and verifies the store before it suggests one, so a pruned
  or expired entry is never offered. Only a tool whose effect is its output can
  be skipped this way: the same read-only predicate the reuse index uses gates
  the index, so a write, a command or a fetch is recorded and suggested never,
  because its bytes are not its outcome. A record also carries the dependency
  stamp of every file its arguments named, on the same size, mtime and hash
  basis `redundancy.py` uses. A lookup whose stamp cannot be reproduced — the
  file changed, or a relative path now resolves from another working directory
  — is never offered, and the record is dropped so the next call does the read.
  Retaining a call identity also needs a
  result of a single text entry, since one recovery id restores one entry. A
  post-tool hook (Claude Code, Codex, MCP, CLI) cannot skip a call that already
  ran, so those surfaces compress results
  instead of skipping
  them. See [redundancy.md](redundancy.md).
- **Request trimming** happens on the Hermes `llm_request` stage when
  `SUTRA_PROMPT=1`: older tool results in the outbound copy are shortened to a
  head, a tail and the recovery marker they already carry, so the removed bytes
  remain restorable by id. The stored conversation is not modified, the newest
  messages are never touched, and a result SUTRA never compressed is retained in
  the recovery store first and then shortened against the id that write returned;
  when the store refuses, that result is left byte-identical. A payload that
  appears repeatedly in the older window keeps its first copy and every later
  copy becomes a pointer to that copy's id, so redundancy is removed before
  size is. Which older results
  go first is a context-value
  decision (`V = w_r R + w_i I + w_d D + w_u U + w_t T` over citation, overlap with
  the latest question, restorability and position), not an age order, and a result
  that a later assistant or user message cites by its recovery id — or refers to by
  a distinctive name it contains, such as a path or a symbol — is never touched.
  A request rewrite changes what the model
  reads, so this stage is opt-in and off in `off` mode; see
  [integrations.md](integrations.md).

  The depth of that trimming follows the mode. Four caps decide it — the size a
  result must exceed, how many results one request may lose, and how large the
  head and tail kept beside the marker are — and on one 539 KB request carrying
  40 recoverable results `bench/request_stage.py` measures: `balanced` leaves
  405,119 bytes (15.9% removed, 8 results), `aggressive` 328,639 bytes (31.8%, 16
  results), `beast` 120,415 bytes (75.0%, 32 results). Every mode keeps a preview
  beside the marker; `beast` keeps a smaller one (384 bytes of head, 192 of tail)
  because a marker alone made an agent re-read the file in pieces, which cost more
  than the removed bytes saved. A result the request cites by its recovery id, or by a distinctive
  name it contains, is still never touched in any mode, and an explicit
  `SUTRA_PROMPT_*` variable replaces the mode default for that cap. Each rewrite
  is counted for the mode that made it, so `sutra.metrics_snapshot()["prompt"]`
  reports live `requests`, `rewritten`, `unchanged` and `saved_bytes` per mode
  instead of only the fixture above.

The nominal bound is not a deadline. The pass count and the payload size cap are
the deterministic bounds, so the same input selects the same plan on an idle and
a loaded machine. Elapsed milliseconds are measured and reported, but they are
not converted into tokens or currency and do not change the winning candidate.

## The decision, in one place

`orchestrator.py` owns the mode policy. For an active mode it:

1. lists the applicable plans;
2. orders them with the SMESP priority rule;
3. runs each plan's bounded passes through the existing engines;
4. validates every accepted candidate with `preserve.preservation_ok`;
5. rejects a plan whose net benefit is not positive (SROF, `SUTRA_SEARCH_OVERHEAD`);
6. ranks the survivors with SEOF and selects the lowest-cost admissible plan
   with SACE, reporting the Pareto front.

The report includes the plans evaluated, the chosen plan, the measured saving,
the trace and the reason when nothing was chosen. A mode with a larger budget
therefore has a larger recorded trace, not a promise of a larger saving.

Quality is `1.0` when the exact validator accepted the candidate and `0.0`
otherwise. Risk is `0.0` because there is no semantic model to estimate it. The
honest description is "unknown, bounded by exact validation", not "safe".

## Structured payloads and patches

JSON documents and patches are never passed to the optimization engines. A patch
is returned byte for byte. A JSON document may still be reduced by the base
engine's string-field elision, with the original recoverable, and must still
parse after the rewrite; the optimization layer does not touch it.

This is deliberate: modes must not turn a patch into a modified diff or make a
JSON document invalid in exchange for a few bytes.

## Migration from `SUTRA_SEARCH`

`SUTRA_SEARCH` is still read and still selects the legacy policies:

| `SUTRA_SEARCH` | Equivalent result |
|---|---|
| `off` (explicit opt-out) | no search |
| `safe` | one formatting pass, Caveman disabled |
| `balanced` | one formatting pass; Caveman only when `SUTRA_LANGUAGE=1` |
| `aggressive` | `SUTRA_SEARCH_PASSES` passes with pair combinations; Caveman only when `SUTRA_LANGUAGE=1` |

Setting `SUTRA_MODE` takes precedence over `SUTRA_SEARCH` but does not erase it:
remove `SUTRA_SEARCH` when you want the mode alone to describe the policy.

`SUTRA_LANGUAGE=1` no longer gates Caveman in `balanced`, `aggressive` or
`beast`; those modes enable it as part of their policy. It still gates Caveman
in the legacy `balanced` and `aggressive` paths.

## Reuse in `off`

Exact-duplicate reuse is disabled when `off` is selected explicitly through
`SUTRA_MODE` or the mode file. It remains enabled for the legacy
`SUTRA_SEARCH=off` path, where `off` only means "no search", so an existing
installation that uses the old variables keeps its previous behavior.
`SUTRA_REUSE=0` disables it in every case.

## Measured behavior

The mode benchmark in `bench/modes.py` compares the four modes through
`core.process_text` with a fresh local store per trial. It reports exact bytes,
`o200k` token counts of what the model would read, whether the text changed,
recovery status, elapsed time and the orchestrator trace. It does not call a
model, does not see a provider's own counters and makes no cost claim: the token
column is a tokenizer measurement of the payload, not a billed-usage figure.

Every committed fixture is smaller than the default `SUTRA_MAX_BYTES`, so none
exercises head/tail elision on its own, while the generated large payloads are
dominated by it. That base elision runs in every mode, `off` included — `off`
disables the optimization engines and the search, not the plugin's own
recoverable content omission — so the `off` column is not a raw-passthrough
baseline.

The recorded run over the eleven workloads (830,619 input bytes, this host,
commit `6468e15` plus the local Caveman change) measured 570,668 bytes in `off`
and 569,837 in each active mode. The 831-byte difference is the wording pass:
22.07% on `verbose-instructions`, 4.53% on `compiler-errors`, 1.79% on
`repeated-test-output`. `aggressive` and `beast` returned the same bytes as
`balanced` on this corpus while spending far more time (summed medians: 0.82 s
balanced, 4.81 s aggressive, 21.06 s beast), because none of these fixtures has
a second applicable transformation for the extra combinations to find. That is a
statement about the corpus, not a guarantee that deeper search never helps: the
modes differ in plans evaluated and passes run, and the recorded trace shows it.

The same run counts tokens as well as bytes, with the `o200k_base` tokenizer
applied to the text the model would read. Bytes and tokens are reported
separately because they are different measurements and do not move together:

| Mode | Bytes out | Byte cut | Tokens out | Token cut | Summed median |
|---|---:|---:|---:|---:|---:|
| off | 570,668 | 31.30% | 157,889 | 33.15% | 0.64 s |
| balanced | 569,837 | 31.40% | 157,756 | 33.20% | 0.82 s |
| aggressive | 569,837 | 31.40% | 157,746 | 33.21% | 4.81 s |
| beast | 569,837 | 31.40% | 157,778 | 33.19% | 21.06 s |

Two things follow from that table and neither is a rounding artefact. The token
cut is not the byte cut: `verbose-instructions` loses 22.07% of its bytes and
24.17% of its tokens, while `synthetic-log` loses 90.02% of its bytes and
89.46% of its tokens, because a saved byte is not a fixed fraction of a token.
And `beast` returning the fewest tokens is noise, not an achievement — the
differences across the three active modes (157,746 to 157,778, a spread of
0.02%) are the wording pass landing on the same text with different search
order, so no mode ranking should be read into them.

`bench/modes.json` is the raw record: it names the commit and host it measured.
Re-running `python3 bench/modes.py` regenerates it for the current checkout,
which is the only way those numbers should be quoted.

## Limits

- A mode cannot make a host call an API it does not expose. Ponytail reuse runs
  in the Hermes middleware because it is the only shipped pre-dispatch frame;
  context selection needs a host that hands SUTRA the conversation before
  prompt assembly, which none of the shipped adapters do (`sutra context` takes
  the items explicitly).
- Caveman is a deterministic prose rule set: a phrase table, a relative-clause
  compaction and a line-opening reminder removal. It does not paraphrase
  sentences or select synonyms, and it abstains on anything that looks like
  code, a command, a path or a template.
- The mode bounds are effort bounds. `beast` tries more combinations; it does
  not guarantee a better result, and it still returns the original when no
  candidate passes validation with a positive saving.
- There is no learned policy. Failure tracking is a bounded local ledger, and
  probabilities are not estimated from it.
