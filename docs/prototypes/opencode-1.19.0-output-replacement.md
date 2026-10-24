# Prototype: OpenCode 1.19.0 tool-output replacement

**Status: feasibility result only.** This is not a production-adapter
validation, not a compression benchmark, and not a general claim about any
OpenCode version other than the one named below. It answers exactly one
question: on this host, can a plugin replace a completed tool's output before
the model consumes it, using only documented APIs?

Related: the architecture audit (Phase 4 ranking), `docs/contract.md` (the
guarantees the core makes), `docs/ab-benchmark.md` (the frozen live-model
protocol — untouched by this prototype).

## Question under test

The audit found a live OpenCode issue reporting that `tool.execute.after`
mutations to `output.output` are "not applied" — without specifying model vs
UI. An adapter is only possible if the **model** receives the replacement.

## Result

**PASS — the replacement reached the model.** On OpenCode 1.19.0, a plugin's
`tool.execute.after` hook replaced the bash tool's 29,701-byte output with the
real SUTRA engine's compressed copy (18,573 bytes, marker included), and the
model-facing tool result contained the marker and not the buried clue:

| Field | Value |
|---|---|
| OpenCode version | **1.19.0** |
| Date | 2026-10-24 |
| Fixture | 29,701 B generated log, clue (`CLUE: the release key is 483920`) buried at line 700 of 1,400 |
| Model-facing tool result | **18,573 B** (head/tail budgets + marker; `head-tail` rule) |
| Marker present in model-facing result | yes — `[sutra: partial · 11,283 bytes elided (large output) · original not retained · …]` |
| Buried clue visible to model | **no** |
| Replacement reached the model | **yes** (asserted on the scripted provider's side, which logs the exact tool-result payload) |

The marker says "original not retained" by design: the prototype exercised
compression only. Retention + recovery is the separate experiment recorded in
[this directory](opencode/README.md) (Phase 2).

## How it was run

Everything ran in an isolated OpenCode tree — the user's `~/.config/opencode`
and `~/.local/share/opencode` were never touched:

```bash
cd /tmp/sutra-oc-proto
nohup python3 fake_model.py > server.log 2>&1 &        # scripted provider on :8909
export XDG_CONFIG_HOME=/tmp/sutra-oc-proto/xdg-config \
       XDG_DATA_HOME=/tmp/sutra-oc-proto/xdg-data
opencode run -m sutra-proto/echo \
  "Read the file /tmp/sutra-oc-proto/fixture/payload.log using bash and tell me its last line."
```

The scripted provider (same technique as the Hermes `scripts/e2e` harness)
forces a `cat` of the fixture, then logs the exact tool-result content it
receives — the ground truth of what the model sees — and writes
`verdict.json`. The plugin (`sutra-proto.js`) mirrors the hook shape of a
plugin already in daily production use on this machine (same `tool.execute.after`
mutation, same fail-open discipline), and pipes the output through
`driver.py`, which loads the real engine from the repository the way Hermes
loads it (package spec). The core was **not modified** for this prototype.

## Files

| File | Role |
|---|---|
| `opencode/sutra-proto.js` | the plugin under test (hook + evidence logging) |
| `opencode/driver.py` | stdin/stdout bridge to the unmodified SUTRA engine |
| `opencode/fake_model.py` | scripted OpenAI-compatible provider on 127.0.0.1:8909 |
| `opencode/verdict.json` | the captured verdict (contents quoted above) |
| `opencode/evidence-feasibility.jsonl` | per-request hook + provider log |

## Known limitations

1. **One host version, one tool, one payload shape.** OpenCode 1.19.0, the
   `bash` tool, a plain-text log. Other tools (read_file's paginated output,
   MCP tool results) were not exercised.
2. **No retention, no recovery in this experiment.** The driver calls
   `sutra.compress` directly with a throwaway home; the marker honestly says
   "not retained". Retention across hook invocations and a registered recovery
   tool are the Phase 2 experiment.
3. **No live model.** The provider is scripted; "the model received it" means
   the model-facing transport received it (that is the thing under test).
4. **Latency unmeasured.** The subprocess hop (JS → Python interpreter →
   engine) is irrelevant for feasibility but is a real adapter-design cost
   (~100 ms per tool result; a persistent sidecar would amortize it).
5. **Prototype-grade paths.** Absolute paths in the scripts; not packaged.
6. The UI presentation of replaced output was not audited — the model-facing
   transport is the only claim.

## What this does not claim

- Not that a production adapter exists or works (it does not exist yet).
- Not that recovery registration works (separate experiment).
- Not any token, cost, or task-success number (that is the frozen A/B
  protocol's job, untouched here).
- Not behavior of any other OpenCode version.
