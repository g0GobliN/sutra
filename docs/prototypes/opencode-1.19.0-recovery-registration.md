# Prototype: OpenCode 1.19.0 recovery-tool registration — **GO**

**Status: feasibility result only** — same standing as the output-replacement
prototype this continues. Not a production-adapter validation, not a
compression benchmark. OpenCode **1.19.0**, real host process, scripted
provider, fully isolated config/data tree (the user's OpenCode setup untouched).

Question: can a plugin register a recovery tool through OpenCode's documented
plugin API such that the **model** can discover and invoke it, and does the
complete loop — compression with retention → marker → recovery → original
content — plus SUTRA's recovery failure contract hold on the real host?

## Verdict: GO

The recovery tool registers, is offered to the model in every session, executes
through the real OpenCode process, and returns the core's JSON envelope
verbatim. All nine contract scenarios pass on the real host (one first-run
failure was a miscalibrated prototype assertion, documented below — the core
behavior was correct). No protocol or engine change was needed to make any
scenario pass.

## Scenario results (each one real OpenCode session; verdicts in `opencode/verdict-*.json`)

| # | Scenario | Assertion | Verdict | Observed |
|---|---|---|---|---|
| 1 | `full-flow` | 29,701 B output → compress+retain → marker with id → model calls recovery → clue returned | **PASS** | `status: ok`, clue recovered; original stored (`stored: true`, id in marker) |
| 2 | `no-recovery` | ordinary compressed path valid without any recovery call | **PASS** | marker present, clue hidden, session completes |
| 3 | `invalid-id` | malformed id → `error` | **PASS** | `invalid id: expected 'su_' followed by 32 hex characters` (verbatim core message) |
| 4 | `unknown-id` | well-formed id never stored → `not_found` | **PASS** | `not_found` + the documented note |
| 5 | `missing-arg` | call without `id` → `error` | **PASS** | reached the plugin's `execute()` and the core rejected it — the host did not silently swallow the call |
| 6 | `expired` | aged entry → `expired` | **FAIL** (assertion), superseded by #7 | host returned `not_found`, not `expired` — see the sweep nuance below |
| 7 | `expired-swept` | contract-conformant outcome for an aged id | **PASS** | `not_found` — recovery.md's `not_found` row explicitly covers "pruned" |
| 8 | `too-large` | `mode=full` above the 8 KiB cap → `too_large` | **PASS** | `too_large`, `original_bytes: 29701` |
| 9 | `repeat` | two identical recovery calls | **PASS** | both `ok`, byte-identical content — idempotent, no host-side dedup interference |
| 10 | `storage-failure` | store dir symlinked → write refused | **PASS** | marker says `original not retained` (no id quoted); restore of an unrelated id → `not_found` |

Tool discovery evidence: `sutra_restore` appeared in the model's tool list in
**every** session (`bash, edit, glob, grep, read, skill, sutra_restore, task,
todowrite, webfetch, write`) — eager visibility, recorded per the adapter
contract's capability 4. This differs from Hermes, where the same tool sits
behind the Tool Search bridge; both are contract-conformant, and the marker's
advice is verbatim-executable on both.

## The sweep nuance (scenario 6 → 7)

On Hermes, an aged-but-unswept entry answers `expired` (deleted on lookup).
On this host's architecture — one fresh driver subprocess per hook/tool call —
the compression that precedes any restore **seeds the store sweep**, which
prunes expired entries before the lookup. An aged id therefore surfaces as
`not_found`, whose documented meaning ("never retained, **pruned**, or wrong
id") already covers it. Both statuses carry the same agent guidance
("re-run the tool with a narrower query"). Verified both paths: driver-level
`expired` (no intervening compression) and host-level `not_found` (sweep
first). No code change needed; the adapter docs must state this consequence of
subprocess-per-call adapters.

## How it was run

```bash
cd /tmp/sutra-oc-proto
export XDG_CONFIG_HOME=/tmp/sutra-oc-proto/xdg-config \
       XDG_DATA_HOME=/tmp/sutra-oc-proto/xdg-data
scripts/ab-like harness:  run-scenarios.sh            # all scenarios, sequential
                          run-scenarios.sh "repeat expired-swept"   # subset
```

Each scenario = one `opencode run -m sutra-proto/echo` session against the
scripted provider on 127.0.0.1:8909. The plugin (`sutra-recovery-plugin.js`)
registers `sutra_restore` with the documented `tool()` helper from
`@opencode-ai/plugin` (installed into the isolated config tree) and replaces
bash output through `tool.execute.after`, skipping its own tool's results.
`driver.py` subcommands (`compress` / `restore` / `seed-expired`) run the real
engine and a persistent store under a scratch `HERMES_HOME`, so hook and tool
subprocesses share state on disk. The fake model logs every request's offered
tools and every tool-result payload — the ground truth of what the model
received — and writes one verdict file per scenario.

Serialization: the recovery JSON envelope passed through the host verbatim
(statuses, `content`, `content_note` intact — parsed by the scripted model and
asserted per scenario). The after-hook replaced only `output.output` on the
bash tool; no other tool was touched. The hook never recompressed a recovery
result (guarded, as the Hermes adapter does).

## What was NOT done

- No production adapter, packaging, or install story.
- No changes to the compression or recovery protocol, the engine, the store,
  Hermes, the frozen A/B assets, or any V5 record.
- No live model (scripted provider throughout — the model-facing transport is
  the thing under test).
- No test of OpenCode versions other than 1.19.0, or of non-bash tools' output
  shapes.

## Remaining production risks (adapter work, not core work)

1. **Latency**: every tool result pays a subprocess hop (JS → Python
   interpreter → engine). Unmeasured here; a persistent sidecar is the likely
   fix and the first adapter-engineering decision.
2. **Store placement**: the prototype pins a scratch `HERMES_HOME`; the adapter
   must map the host's profile identity to a private store directory (core
   already accepts an injected directory).
3. **Sweep-first semantics** (above): adapter docs must tell operators that
   `expired` is rare on subprocess adapters.
4. **Tool surface**: the prototype registers one tool with a minimal schema;
   production should mirror the full `TOOL_SCHEMA` argument set and description.
5. **Version coupling**: OpenCode 1.19.0 only; the hook/tool API contract needs
   a pinned compatibility statement per release.
6. **UI framing of replaced output** was not audited (model-facing transport
   only).

## Smallest next step

A packaging spike, still not the adapter: turn the prototype's plugin + driver
into an installable unit with the store directory derived from the host config
home and a persistent-engine sidecar, then re-run these same ten scenarios as
its acceptance suite.
