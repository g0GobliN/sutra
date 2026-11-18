# Validating SUTRA inside Hermes

This page documents how SUTRA is exercised in a real Hermes install, what the
harness measures, and what it cannot measure. Every number here comes from a
run of the commands below on 2026-11-18; nothing is extrapolated from the
offline benchmark.

## What is installed, and where

Hermes loads a directory plugin from `$HERMES_HOME/plugins/<name>`. The
production install on this host is `~/.hermes/plugins/sutra`, version 0.6.3,
which is the public release and does **not** contain the branch modules
(`cache.py`, `context.py`, `redundancy.py`, `providers.py`). Running the
release is not evidence about the branch.

The harness therefore builds an isolated home:

```bash
python bench/agent/isolated_home.py --home ~/.hermes-sutra-ab --force
HERMES_HOME=~/.hermes-sutra-ab hermes plugins list    # sutra, source user
```

`isolated_home.py` copies the modules of this checkout into
`~/.hermes-sutra-ab/plugins/sutra`, writes a config that points the model at
the local provider, and marks the directory so it can never overwrite a real
home. The production `~/.hermes` is not read or written by any of this.

## The measurement point

`bench/agent/mock_provider.py` is an OpenAI-compatible server. In its default
mode it answers deterministically from a scenario file: one `terminal` tool
call per request, then a final message. It logs, per request, the serialized
message bytes, an o200k token count (tiktoken when importable), and every tool
result's size. That is the number the model actually had to carry.

With `--upstream https://openrouter.ai/api/v1` the same server forwards to a
real provider and records the provider's own `usage` object. The key is read
from `OPENROUTER_API_KEY` and never logged.

## Why the gateway has to be restarted

`hermes -z` does not run the turn in the CLI process. It ensures a gateway
daemon and hands the turn to it, and that daemon keeps the environment of its
first start. The first attempt at a three-mode comparison silently ran all
three conditions with `SUTRA_SEARCH=off` from one daemon:

```
14:17:04  search_mode "off"   search_applied ""
14:17:09  search_mode "off"   search_applied ""
14:17:15  search_mode "off"   search_applied ""
```

`bench/agent/run_abc.py` stops the gateway before every condition, so each
condition starts the process it configured. A record whose `search_mode` does
not match the condition is a harness bug, not a data point.

## The proof that a mode ran

`engine.CompressionStats` now carries `search_mode`, `search_applied` and
`search_saved_bytes`, and `metrics` writes them into the stats record. The
record is the evidence: a mode that did not execute cannot produce its name,
and a strategy that did not run cannot produce its name either.

A JSON tool result is compressed field by field, and the search layer runs on
each decoded field, because that is where prose is visible as prose. The
document record used to drop those names; it now carries them.

Measured on the 3,000-line prose payload (51,014 B tool result):

| Mode | Tool result after SUTRA | Model request | search record | SUTRA duration |
| --- | --- | --- | --- | --- |
| off | 9,972 B | 4,572 tokens | none | 116.75 ms |
| balanced | 6,071 B | 3,590 tokens | `language`, 3,901 B | 377.33 ms |
| aggressive | 6,071 B | 3,590 tokens | `language`, 3,901 B | 1,608.80 ms |

Aggressive did not beat balanced on this payload and spent roughly four times
the processing time looking. That is a measurement, not a bug: the combination
search has nothing to combine when one strategy already removes the filler.

## The corpus

`bench/agent/build_corpus.py` writes 15 scenarios with the same shape:
reproducible shell commands, a category, and a prediction (`helpful`,
`neutral`, `no-benefit`) to check rather than to trust. Payloads range from a
repository listing, a traceback and a test-collection dump to a patch, a
128 kB numeric array and a large prose block.

Assertion budget for a corpus addition: the command must be deterministic, its
output must not depend on this host's local time or user, and the scenario must
say what it expects.

## Reproducing

```bash
python bench/agent/build_corpus.py
python bench/agent/run_abc.py --corpus bench/agent/corpus \
    --out bench/agent/results/abc-corpus.json
```

Four conditions run per scenario: `baseline` (plugin loaded, `SUTRA_ENABLED=0`),
`compression-only` (deterministic compression, search off), `balanced`, and
`aggressive`. `SUTRA_REUSE=0` everywhere so a repeated result cannot silently
skip compression.

For a live provider run:

```bash
set -a; . ~/.hermes/.env; set +a
python bench/agent/run_abc.py --scenario bench/agent/corpus/06-long-context.json \
    --model nvidia/nemotron-3.5-lightning:free \
    --upstream https://openrouter.ai/api/v1 \
    --out /tmp/abc-live.json
```

A `:free` OpenRouter model was verified to work through the proxy and return
real `usage` with `cost: 0` (201 total tokens for a one-line reply, 175 of them
reasoning). Paid models need a spend authorization from the repository owner;
none was used for any result on this page.

## What is not measured

- **Billed cost.** The deterministic scenarios never touch a paid provider. The
  live path records provider `usage`, but no paid call was made.
- **Task quality against a live model.** The deterministic provider always
  follows the scenario, so A/B/C differ only in what reaches the model. Quality
  under a real model needs a scored rubric and a budget, which is listed as
  remaining work.
- **Provider prompt-cache hits.** A post-tool-result middleware cannot see or
  influence the provider's prompt cache, so no cache saving is claimed.
