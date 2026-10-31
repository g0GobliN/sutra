# Configuration

Every setting is read on each call, so a change takes effect without restarting
the process. Defaults are a disk and context commitment, not just tuning knobs.

| Variable | Default | Meaning |
|---|---|---|
| `SUTRA_ENABLED` | `1` | `0` disables compression entirely, and retention with it |
| `SUTRA_MAX_BYTES` | `24576` | results at or below this are untouched; larger results are rewritten to fit |
| `SUTRA_HEAD_BYTES` | `12288` | bytes kept from the start |
| `SUTRA_TAIL_BYTES` | `6144` | bytes kept from the end (scaled down proportionally if head and tail exceed the cap) |
| `SUTRA_STATS_FILE` | `$HERMES_HOME/cache/sutra-stats.jsonl` | where savings are recorded |
| `SUTRA_STATS_MAX_BYTES` | `5242880` | rotate the log at this size (one `.1` file is kept) |
| `SUTRA_STORE` | `1` | `0` stops retaining originals; markers then say "not retained" |
| `SUTRA_STORE_DIR` | `$HERMES_HOME/cache/sutra-store` | where originals live |
| `SUTRA_STORE_TTL_HOURS` | `168` | how long an original is kept (seven days) |
| `SUTRA_STORE_MAX_BYTES` | `134217728` | total size of the store (128 MiB) |
| `SUTRA_STORE_MAX_ENTRY_BYTES` | `33554432` | largest single original retained (32 MiB) |
| `SUTRA_STORE_MAX_ENTRIES` | `2000` | most originals retained |
| `SUTRA_RECOVERY_MAX_BYTES` | `8192` | default response cap for `sutra_restore` (64 KiB hard maximum) |
| `SUTRA_RECOVERY_MAX_MATCHES` | `5` | default matching lines per search (20 hard maximum) |
| `SUTRA_JSON_ITEMS` | retired | ignored; a warning is logged if set |

## Store behaviour

Originals live under `$HERMES_HOME/cache/sutra-store/` by default, or under
`SUTRA_STORE_DIR` when set. The directory is created `0700`; files are written
atomically as `0600`; ids are 128-bit random values validated before any path is
built. Retention is bounded by the entry, total-size, count and age limits above.

The store is **not encrypted**. Tool output can contain secrets, and any process
running as the same user can read the store. Set `SUTRA_STORE=0` if that is not
acceptable: compression keeps working, but markers say the original was not
retained and quote no id.

## Recovery bounds

`sutra_restore` returns at most `SUTRA_RECOVERY_MAX_BYTES` per response (8 KiB by
default, 64 KiB hard maximum) and at most `SUTRA_RECOVERY_MAX_MATCHES` matching
lines per search (5 by default, 20 hard maximum). `mode="full"` refuses above the
response cap; explicit `bytes` and `lines` pages remain available. A single line
longer than the cap is returned clipped.

## Provider-side isolation

The store follows `HERMES_HOME` / `SUTRA_STORE_DIR` per profile. A Claude Code
hook and its MCP server must use the same store, or a marker written by one is
`not_found` in the other. Set `SUTRA_STORE_DIR` (or `HERMES_HOME`) in both
environments when you configure them separately.

The API form is `sutra.configuration()`, which returns every resolved value as a
frozen, inspectable object.
