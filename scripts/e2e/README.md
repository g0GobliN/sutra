# End-to-end integration harness

Proves SUTRA's **integration** — not its compression functions in isolation:
real Hermes agent loop, real terminal tool, real middleware, real store, and
`sutra_restore` reached the way a current Hermes host actually exposes it.
No model provider and no credentials are needed: the scripted model in
`fake_model.py` *is* the provider, so the whole loop is offline and free.

## What it proves

One pass exercises, in order:

1. **Compression on the live path.** The scripted model has Hermes' `terminal`
   tool run a command whose output is ~39 kB with a clue buried in the middle —
   under Hermes' own 50 kB terminal cap, over SUTRA's 24,576 B threshold. The
   result the model receives must carry a `[sutra: partial …]` marker, name the
   field, quote a real id, and **not** contain the clue.
2. **Retention.** The id in the marker resolves against the real store
   (`$HERMES_HOME/cache/sutra-store/`), whose entries were written by the
   middleware during the same session.
3. **Tool visibility under Tool Search.** Current Hermes hosts defer plugin
   tools behind the `tool_search`/`tool_describe`/`tool_call` bridge. The script
   must find `sutra_restore` through `tool_search` — the same path a real model
   has to walk.
4. **Recovery.** `sutra_restore` is called through the bridge with the marker's
   id, `mode="search"`; the response must be `status: ok` and contain the clue.

The verdict is written to `$SUTRA_E2E_OUT/verdict.json` and echoed; `run.sh`
exits 0 only when every step held. `transcript.jsonl` records every request the
host made, for diffing a failure.

## Preconditions

- Hermes Agent installed with the gateway available (`hermes gateway status`).
- This plugin installed and **enabled**: `hermes plugins enable sutra`.
- The `sutra` toolset enabled for `cli` (`hermes plugins enable` adds it).
- **The gateway must have finished plugin discovery** after (re)starting:
  discovery is a background thread and sessions started too early see no plugin
  tools. `grep "Plugin discovery complete" ~/.hermes/logs/agent.log | tail -1`
  should postdate the last gateway restart.
- The terminal tool's own output cap (`tool_output_limits`, default 50,000 B)
  must sit **above** the payload and SUTRA's `SUTRA_MAX_BYTES` (24,576 B) below
  it; the default `--command` is sized for both defaults.

## Running it

```bash
scripts/e2e/run.sh                      # prints the verdict JSON, exits 0/1
SUTRA_E2E_OUT=/tmp/myrun scripts/e2e/run.sh
```

Environment: `SUTRA_E2E_PORT` (8907), `SUTRA_E2E_OUT` (/tmp/sutra-e2e),
`SUTRA_E2E_QUERY`, `SUTRA_E2E_TIMEOUT` (240 s).

Verified against Hermes Agent v0.22.2+3030.g4d04303 (2026-10-24): 2/2 runs,
`ok: true` — compression, marker, deferred-tool discovery, and recovery all
through the real gateway. The harness is deliberately not part of CI: it needs
a live Hermes installation and is meaningless on a bare runner.
