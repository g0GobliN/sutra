# Production OpenCode adapter — runtime proof (1.19.5)

Captured 2026-10-31 on Linux with the installed OpenCode **1.19.5** binary and
`sutra-agent` **0.3.0** built from this checkout. No live model or paid provider
was called. The scripted OpenAI-compatible provider records every request body,
so its log is the model-facing evidence.

## Setup

- isolated `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`,
  `XDG_STATE_HOME`, and `SUTRA_OPENCODE_HOME`;
- `~/.config/opencode/opencode.json` contains only the scripted `poc-local`
  provider and normal permissions;
- `sutra install --client opencode` wrote
  `~/.config/opencode/plugins/sutra.js` (mode 0600) and the MCP recovery entry;
- the generated plugin embeds the installed wheel's interpreter and
  `/tmp/oc-prod-sutra` as its store home;
- the scripted provider and OpenCode ran in `/tmp/oc-prod-work`.

The shell command that generated the result is in
`spikes/opencode-poc/fake_model_oc.py`; the scenario is `treatment`.

## Result

| Requirement | Observed |
|---|---|
| Real Bash tool executed | yes — scripted 1,501-line command |
| SUTRA received the result | yes — `tool.execute.after` ran the installed CLI as a subprocess |
| Model-facing result was replaced | **48,436 B → 18,649 B** in `requests.jsonl` |
| Marker and recovery id present | `marker: true`, id `su_627169eafca232b21c47f1c433d96ee3` |
| Clue absent from the model-facing copy | `clue_visible: false` |
| Recovery through the host | OpenCode dispatched `sutra_restore`; statuses `["error", "not_found", "ok"]` |
| Exact evidence recovered | `payload_bytes: 48436`, `status: ok`, `content` contains the buried `ZEBRA-7Q` line |
| Session completed | `RUN_EXIT=0`, verdict `OC-SUTRA-OK` |

Relevant request log lines:

```json
{"event":"tool_result","bytes":18649,"marker":true,"clue_visible":false,"entry":"su_627169eafca232b21c47f1c433d96ee3"}
```

Verdict file:

```json
{
  "ok": true,
  "marker_seen": true,
  "clue_visible_in_model_input": false,
  "clue_recovered": true,
  "restore_statuses": ["error", "not_found", "ok"]
}
```

The restore response reported `payload_bytes: 48436`, `total_lines: 1501`,
`matches_found: 1`, and returned lines 750–752 verbatim, including the clue.

## Boundaries

- Scripted provider, not a live model: this proves the host's output-replacement
  and recovery pipeline, not task quality or billing.
- OpenCode 1.19.5 only; the hook contract must be re-checked after host upgrades.
- Linux only; Windows was not exercised.
- The adapter is fail-open by design, so a missing interpreter or a timeout
  leaves the original result untouched; that behavior is covered by the Node
  hook tests, not this successful scenario.
