# Three live runs against a real provider

The corpus run uses a scripted provider, so it can compare request sizes but cannot
produce real usage numbers. This page records what happens when the same harness talks
to a real model through the local proxy: actual provider usage, actual cache fields, and
the two final answers side by side.

## Setup

- Model: `nvidia/nemotron-3.5-lightning:free` through OpenRouter. Free tier, so the
  recorded cost is 0 and no spend was authorised or incurred.
- The proxy forwards each request to the provider and logs the returned `usage` object
  unchanged. The key is read from the environment and never logged.
- Conditions: `baseline` (`SUTRA_ENABLED=0`) and `balanced` (search `balanced`,
  language on, four passes). The gateway is restarted per condition and the isolated
  home is rewritten per condition, as in the corpus run.
- The prompt tells the agent the exact command to run, taken from the corpus scenario,
  so both conditions execute the same tool call.

## Usage

| Scenario | Condition | Requests | Final request tokens | Prompt tokens (usage) | Cached input | Output tokens | Tool result bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| 03 debugging traceback | baseline | 3 | 32,998 | 58,794 | 23,232 | 550 | 52,232 |
| 03 debugging traceback | balanced | 2 | 17,098 | 29,803 | 25,344 | 450 | 11,342 |
| 11 repeated context | baseline | 2 | 27,221 | 39,936 | 12,672 | 424 | 50,978 |
| 11 repeated context | balanced | 2 | 14,861 | 27,576 | 25,344 | 516 | 6,076 |
| 12 structured JSON | baseline | 48 | 19,022 | 31,722 | 12,672 | 752 | 18,354 |
| 12 structured JSON | balanced | 25 | 19,023 | 31,723 | 25,344 | 492 | 18,354 |

The 03 and 11 rows are a clean comparison: one tool call, two or three model calls,
and the compressed result is 78% and 88% smaller. The provider-billed prompt tokens
fall by 49% and 31% respectively. Output tokens are close, which is expected: the
answer is a short summary in both cases.

The 12 row is not a comparison of SUTRA. The scenario prints a JSON array, SUTRA leaves
that payload untouched (a documented no-benefit case), and the free model looped: 48
requests in the baseline run against 25 in the balanced run, with the same tool result
in both. It is useful only as a reminder that agent loops are noisy and that a single
run is not an effect size.

## Cache fields are real, and they cut both ways

Provider prompt caching shows up in this setup. In the 03 baseline run the second
request had 23,232 of its prompt tokens served from cache; in the balanced run the
cached count was 25,344. That is the provider's accounting, recorded from its own usage
object, not inferred by SUTRA.

Two consequences:

1. The post-tool-result design rewrites the tail of the conversation, not the prefix,
   so the cached prefix survives and the compression saving is additive to the cache
   discount rather than competing with it.
2. A run that changed earlier messages between calls would reduce that cache hit rate.
   Any future pre-call rewrite has to be measured against these numbers
   (`docs/next-architecture.md`, option 1).

## Quality on the two comparable runs

The answers are equivalent in content:

- 03 baseline: lists the 500-frame pattern, the `ValueError: bad input in parser at
  offset 42` footer, the 52,250-character length and the saved-output path.
- 03 balanced: same footer and pattern, and it even reports that SUTRA elided the middle
  ~39,660 bytes and kept the error end.
- 11 baseline: 3,000 lines, one per worker, `worker 0` through `worker 2999`.
- 11 balanced: the same range and format, with the first and last lines shown.

That is a sanity check, not a quality score. Two answers read by hand cannot detect a
regression that only appears on the tasks where a protected detail matters. A scored
evaluation needs the protocol in `docs/ab-benchmark.md`: fixed tasks, an evaluator, and
enough runs to separate SUTRA's effect from model noise.

## What is still missing

- A scored quality comparison on real tasks.
- More than one run per condition; the free tier is rate limited and the model loops
  unpredictably, so repeated runs are needed before treating any percentage as stable.
- A paid model would give non-zero cost numbers, but that needs explicit authorisation.

## Reproduction

```bash
python bench/agent/run_abc.py \
  --scenario bench/agent/corpus/03-debugging.json \
  --conditions baseline,balanced \
  --model nvidia/nemotron-3.5-lightning:free \
  --upstream https://openrouter.ai/api/v1 \
  --prompt "Run exactly this shell command with the terminal tool, then summarise what it printed: <command>" \
  --out /tmp/live.json --retries 1
```

The proxy reads `OPENROUTER_API_KEY` from `~/.hermes/.env` when it is not already in the
environment. No other variable from that file is imported, because a chat-platform token
in the environment makes the isolated gateway fail to start.
