# Optimization modes

SUTRA exposes four modes: `off`, `balanced`, `aggressive` and `beast`. A mode is a
resource and search-effort policy. It is not a different compression guarantee,
and it does not change the protected-span rules: every candidate in every mode
must survive the same exact preservation check before it can replace a byte.

`off` is the default. An installation that has not set `SUTRA_MODE` behaves as it
did before modes existed.

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
4. `off`.

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
- **Caveman** removes filler and repeated prose from ordinary sentences. It
  skips code, fenced blocks, commands, quoted material and any line carrying
  shell or template syntax. It is a fixed rule set, not a rewriting model.
- **Ponytail** is exposed through `decide_execution()` for hosts that control
  tool dispatch. It classifies a repeated call as `SAFE_TO_REUSE`,
  `REQUIRES_REVALIDATION`, `MUST_EXECUTE` or `UNKNOWN` from dependency
  snapshots. Tests, lint, type checks and security scans are never reused, in
  any mode. A post-tool hook cannot skip the call that already ran, so the hook
  itself does not perform Ponytail reuse; the host adapter must call it before
  dispatch.

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
5. rejects a plan whose byte saving is not positive (SROF);
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
| `off` (default) | no search |
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
`SUTRA_MODE` or the mode file. It remains enabled under the legacy default,
where `off` only means "no search", so an existing installation that sets
nothing keeps its previous behavior. `SUTRA_REUSE=0` disables it in every case.

## Measured behavior

The mode benchmark in `bench/modes.py` compares the four modes through
`core.process_text` with a fresh local store per trial. It reports exact bytes,
whether the text changed, recovery status, elapsed time and the orchestrator
trace. It does not call a model and makes no provider-token or cost claim.

Every committed fixture is smaller than the default `SUTRA_MAX_BYTES`, so none
exercises head/tail elision. Two are still improved by the lossless wording
pass (892 → 876 bytes of repeated test output, 265 → 253 bytes of compiler
errors); the others have no applicable plan. On the generated large payloads
the byte reduction is dominated by the base head/tail elision. The active modes
differ in the plans they evaluate and in the recorded decision, not in a large
additional saving on that corpus. That result is reported as measured rather
than rounded up into a claim.

## Limits

- A mode cannot make a host call an API it does not expose. Ponytail reuse and
  context selection require a host adapter that runs before dispatch or before
  prompt assembly.
- Caveman is a fixed prose rule set. It does not paraphrase sentences, and it
  abstains on anything that looks like code, a command, a path or a template.
- The mode bounds are effort bounds. `beast` tries more combinations; it does
  not guarantee a better result, and it still returns the original when no
  candidate passes validation with a positive saving.
- There is no learned policy. Failure tracking is a bounded local ledger, and
  probabilities are not estimated from it.
