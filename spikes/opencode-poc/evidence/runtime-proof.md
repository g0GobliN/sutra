# Runtime proof - captured 2026-10-24, OpenCode 1.19.0 (installed binary)

Environment: project-scoped config in /tmp/oc-poc (own opencode.json, own
.opencode/plugins/sutra-spike.js), XDG_CONFIG_HOME/XDG_DATA_HOME pointed at an
empty directory (no user plugins, no MCP servers), provider = scripted
OpenAI-compatible endpoint on 127.0.0.1:8908 whose request log IS the
model-facing evidence, SUTRA bridge on 127.0.0.1:8765 with
HERMES_HOME=/tmp/sutra-opencode-home.

## Treatment (bridge running)

| Step | Evidence |
|---|---|
| bash tool ran the scripted 1,501-line command | raw output 48,436 bytes; clue ("ZEBRA-7Q") at line 750 |
| `tool.execute.after` hook -> bridge -> sutra.compress_result | model-facing tool result: **18,649 bytes with a `[sutra: partial ...]` marker** and the id su_809110c2... |
| model received the compressed copy | requests.jsonl: `clue_visible_in_model_input: false` |
| model called the plugin-registered `sutra_restore` | OpenCode executed it as a normal tool (`⚙ sutra_restore {...}`) |
| recovery | `status: ok`, lines 750-752 returned verbatim, `payload_bytes: 48436`, sha-backed store entry in the isolated HERMES_HOME |
| verdict (scripts) | `OC-SUTRA-OK: marker_seen=true clue_recovered=true` |

## Fail-open (bridge killed first)

| Step | Evidence |
|---|---|
| hook fetch times out after 2 s | caught, swallowed |
| model-facing tool result | **48,436 bytes, no marker** - the untouched result |
| session | completed normally; no crash, no missing tool result |
