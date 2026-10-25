# Troubleshooting

Every failure mode SUTRA can produce, with its cause and remedy. For the
recovery protocol itself see [recovery.md](recovery.md); for the security
model see [../SECURITY.md](../SECURITY.md).

## Restore statuses and markers

| Symptom | Likely cause | What to do |
|---|---|---|
| `status: expired` | TTL or quota cleanup removed the original | re-run the tool narrowly; raise `SUTRA_STORE_TTL_HOURS` before the fact |
| `status: too_large` | the scope is above the 8 KiB response cap | ask for `mode="bytes"` or `mode="lines"` with a range, or a literal `search`; `max_bytes` may rise to 65,536 |
| `status: unavailable` | `SUTRA_STORE=0`, or the store path is not a plain directory | re-enable the store; use a normal directory for `SUTRA_STORE_DIR` |
| `status: not_found` | wrong, expired or pruned id, or a corrupt entry deleted on lookup | re-run the tool; do not invent ids |
| `status: error` | malformed field pointer, range, or query | read `error`; fix the argument |
| marker has no id | not retained: store off, over quota, entry too large, or write failed | re-run narrower; check `SUTRA_STORE*` |

## The tool is not visible to the model

| Symptom | Likely cause | What to do |
|---|---|---|
| `sutra_restore` unknown / no tool | toolset `sutra` is disabled for the platform — or Hermes' Tool Search deferred it with the other plugin tools | enable the toolset in Hermes; with Tool Search active the model must `tool_search` for the tool first (the marker's advice stays actionable through the bridge), or pin `sutra_restore` eager via `tools.tool_search` |

With `plugins.isolation: host`, the middleware runs in Hermes's plugin host
process. Measured on a cold gateway restart, the plugin registers within the
first seconds of boot; the periodic "Plugin discovery complete" log lines are
later re-discovery cycles, not an availability gate. If a session reports
`sutra_restore` unknown while the toolset is enabled, the cause is Tool Search
deferral, not timing.

## Plugin state

| Symptom | Likely cause | What to do |
|---|---|---|
| plugin not loaded, or old code runs | disabled, not installed, or a stale copy shadows the checkout | `hermes plugins list`, `hermes plugins show sutra`, `hermes plugins validate .`; remove the stale `plugins/sutra` copy |
| store keeps growing | TTL/quota are larger than expected | inspect `sutra.metrics_snapshot()`; lower `SUTRA_STORE_TTL_HOURS`, `SUTRA_STORE_MAX_BYTES`, `SUTRA_STORE_MAX_ENTRIES` |

A stale installed copy can win over a checkout you are editing — that is the
first thing to check when a change does not appear.

## Removing it

If Hermes installed it:

```bash
hermes plugins disable sutra
hermes plugins remove sutra
```

`remove` takes the plugin directory name shown by `hermes plugins list`. If
the plugin was copied or symlinked into place instead, delete that directory —
`$HERMES_HOME/plugins/sutra` (usually `~/.hermes/plugins/sutra`), or
`./.hermes/plugins/sutra` for a project-local install — and restart Hermes.

Removal does not delete retained output or its log. To remove those as well:

```bash
rm -rf "$HERMES_HOME/cache/sutra-store" "$HERMES_HOME"/cache/sutra-stats.jsonl*
```

This permanently deletes every retained original; back up anything you need
first.
