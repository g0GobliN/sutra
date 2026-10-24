# SUTRA × OpenCode feasibility spike

**Verdict: GO** — OpenCode's `tool.execute.after` hook passes the replaced
output to the model, and SUTRA's recovery contract survives intact. Evidence
below is from the installed OpenCode 1.19.0 running real sessions, not from
documentation or mocked hooks.

This spike is deliberately **not** the production adapter: no engine changes,
no Hermes changes, no merge to main. It proves the integration point and
identifies what a production adapter would need.

## 1. The integration point (from the actual implementation)

Three independent sources agree on the mechanism:

1. **Hook type** (`packages/plugin/src/index.ts`, v1.19.2): the
   `tool.execute.after` hook is `(input, output: {title, output, metadata}) =>
   Promise<void>` — it **returns void**. Replacement is in-place mutation of
   `output.output`; hook return values are not used.
2. **Pipeline** (installed v1.19.0 binary bundle, tool execution path): the
   tool's result object is created, passed by reference into
   `trigger("tool.execute.after", input, V)`, and that **same object** is what
   the pipeline returns to the session — so a hook's mutation is exactly what
   the model consumes.
3. **Working precedent on this install**: the user's own `caveman-blocks.js`
   plugin appends to `output.output` in the same hook and its effects are
   visible to models.

So: **replacement works by mutating `output.output` inside the hook.** The
runtime proof below confirms it end to end.

## 2. The proof of concept

Three small pieces (this directory):

| File | Role |
|---|---|
| `bridge.py` | loopback HTTP server (127.0.0.1) holding the **real, unmodified SUTRA package** in-process; isolated `HERMES_HOME` (default `/tmp/sutra-opencode-home`, never `~/.hermes`); 2 MiB body cap; **startup-generated token** (0600 file, required as `X-Sutra-Token` on every POST); `GET /health` (no storage paths), `POST /compress` (runs `sutra.compress_result`), `POST /restore` (serves `sutra.recovery.restore` verbatim) |
| `plugin/sutra-spike.js` | one OpenCode plugin: the `tool.execute.after` hook (string outputs only; 2 s fetch timeout; fail-open on any error; never touches `title`/`metadata`) and the `sutra_restore` tool registered through the factory's `tool` map — served by the real recovery protocol |
| `fake_model_oc.py` | scripted OpenAI-compatible provider; **its request log is the model-facing evidence** |

### Reproduction (exact commands)

```bash
cd /home/goblin01/github_projects/tooltrim   # repo root; spike branch
git checkout spike/opencode-integration      # this branch

# 1. unit tests for the bridge (offline, real SUTRA, real HTTP)
.venv/bin/python -m pytest -q spikes/opencode-poc/test_bridge.py   # 10 passed

# 2. isolated OpenCode project: plugin + a config pointing at the fake provider
#    (re-copy the plugin after every edit - a stale copy silently fails open)
mkdir -p /tmp/oc-poc/.opencode/plugins
cp spikes/opencode-poc/plugin/sutra-spike.js /tmp/oc-poc/.opencode/plugins/
# write /tmp/oc-poc/opencode.json: custom provider -> http://127.0.0.1:8908/v1,
# permissions allowed, autoupdate/share disabled (content in evidence/)

# 3. start the bridge and the scripted provider
python3 spikes/opencode-poc/bridge.py &            # 127.0.0.1:8765, isolated home
python3 spikes/opencode-poc/fake_model_oc.py &     # 127.0.0.1:8908

# 4. run a real OpenCode session (no real provider credits; all local)
cd /tmp/oc-poc && rm -f requests.jsonl verdict.json
XDG_CONFIG_HOME=/tmp/oc-xdg XDG_DATA_HOME=/tmp/oc-xdg/data \
  opencode run "Run a bash command that produces a large output, then find the
  CLUE token in it. Follow any marker instructions to retrieve omitted
  evidence. Finally report the clue."

# 5. read the verdict and the model-facing evidence
cat /tmp/oc-poc/verdict.json
grep tool_result /tmp/oc-poc/requests.jsonl
```

`XDG_CONFIG_HOME`/`XDG_DATA_HOME` point OpenCode at empty directories so the
user's global plugins and MCP servers stay out of the experiment, and so
nothing here can disturb the running V5 benchmark: **no gateway restarts, no
Hermes plugin state changes, no shared store** (the bridge's
`HERMES_HOME=/tmp/sutra-opencode-home` is disjoint from `~/.hermes`).

## 3. Results (2026-10-24, OpenCode 1.19.0 installed binary)

Treatment and fail-open evidence: [evidence/runtime-proof.md](evidence/runtime-proof.md),
raw verdicts in [evidence/](evidence/).

| Requirement | Result |
|---|---|
| Model-facing output contains the compressed result and marker | **yes** — model-facing tool result was 18,649 bytes with `[sutra: partial …]`; the raw tool output was 48,436 bytes |
| Original remains recoverable | **yes** — the model called the plugin-registered `sutra_restore`; real SUTRA answered `status: ok` with the exact buried lines (750–752) from the sha-backed store entry |
| Invalid/unknown references expose nothing | **yes, in the real host** — the scripted model called `sutra_restore` with a malformed id (`status: error`) and a well-formed unknown id (`status: not_found`) through OpenCode's real tool dispatch before the real id (`ok`); verdict records `["error", "not_found", "ok"]` |
| Recovery failures fail open | **yes** — bridge killed: hook times out at 2 s, the model receives the untouched 48,436-byte result, session completes; store disabled → `status: unavailable` (bridge test); tokenless/wrong-token requests → 401, and the hook's fail-open keeps the session working (observed live with a stale plugin copy) |
| Diffs and metadata preserved | **yes, in the real host** — a 48,482-byte diff reached the model byte-intact with no marker; the hook only ever rewrites `output.output` for string outputs; `title`/`metadata` are never touched |
| No unbounded subprocess/storage/output risk | **yes** — one operator-started bridge process (SUTRA in-process, no per-call subprocesses), 2 s hook timeout, 2 MiB request cap, loopback bind + token, isolated store with SUTRA's own 128 MiB/32 MiB/2,000-entry/TTL bounds. **Scope note on the oversize observation**: the 3.6 MB raw output arriving as a bounded 915-byte copy is evidence of the *combined* pipeline (OpenCode truncates tool output before the hook, then SUTRA's collapse runs) — it is **not** proof that SUTRA alone handles arbitrary oversized inputs. SUTRA's own oversized-input paths are covered independently: the bridge's 2 MiB body cap (unit-tested 413) and the engine's 64 MiB `too-large-kept` refusal |

Also observed: OpenCode's bash tool passed the full 48,436-byte output through
untruncated on this version, so SUTRA's default 24,576-byte threshold engaged
without any configuration; giant (multi-MB) outputs are truncated by OpenCode
itself before the hook, so the model-facing copy stays bounded at every layer.

## 4. Verdict

**GO** for building a production OpenCode adapter. The hook contract does
exactly what SUTRA needs, the recovery tool registers as a normal OpenCode
tool, and the failure semantics (fail-open, bounded work) carry over.

## 5. What a production adapter would still need (out of scope here)

1. **Packaging and lifecycle**: ship the plugin (npm package or committed
   `.opencode/plugin`), and decide how the bridge starts — a plugin-spawned
   child with a lockfile, a user service, or an `opencode` plugin-host
   equivalent. This spike runs it by hand.
2. **Configuration surface**: thresholds and store policy per project
   (`SUTRA_MAX_BYTES`, `SUTRA_STORE*`), bridge URL discovery, and a documented
   `HERMES_HOME` story for OpenCode users (the store path naming is
   Hermes-specific today).
3. **Security**: the bridge is unauthenticated loopback; a production version
   needs a token or a Unix-socket pair, plus a review of which tools are
   compressed (an allowlist/denylist, mirroring the Hermes `_NO_COMPRESS_TOOLS`
   idea for its own restore tool).
4. **Testing**: CI tests cannot run OpenCode sessions (no provider, no GUI
   assumptions); the honest layering is bridge unit tests (landed here) plus a
   scripted-provider runtime proof run manually or in an optional workflow.
5. **Extraction of the shared core**: the bridge imports the repo package by
   path; a production adapter wants the engine/store/recovery core published
   (or vendored) independently of the Hermes adapter — the same "core vs
   adapter" split noted in the mission's maintainability phase.
6. **OpenCode version coupling**: the hook contract was verified against
   1.19.0; the plugin loader rejected non-function exports, and the tool map
   mechanism differs across versions (the repo's `dev` branch is mid-refactor
   to an Effect-based plugin system). Pin and re-verify per release.
