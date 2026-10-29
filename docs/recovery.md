# The recovery protocol

This is the contract between SUTRA and the agent that receives its output.
It exists so that a shortened tool result is never mistaken for a complete one,
and so that the removed bytes can be fetched without trusting the model to
invent them.

## 1. Identity

Every retained original gets an id:

```
su_0123456789abcdef0123456789abcdef
```

`su_` plus 32 lowercase hex characters from `secrets.token_hex(16)` — 128 bits
of randomness. The id is the only handle the model ever sees; it contains no
path, no session and no timestamp. Requests are validated against
`^su_[0-9a-f]{32}$` before any filesystem path is constructed, so an id cannot
name a file outside the store.

Ids are scoped to one Hermes profile: the store lives under
`$HERMES_HOME/cache/sutra-store/`, and two profiles do not share it. An id
from another profile is reported as `not_found`.

## 2. The marker

A rewritten result carries a single-line marker in place of the omitted bytes.

**Top-level elision** (plain text, traceback, or a whole-payload elision):

```
[sutra: partial · 133,522 bytes elided (large output) · id=su_… ·
 original 151,920 B retained 168h ·
 retrieve: sutra_restore(id="su_…", mode="bytes", start=12259, end=145781)]
```

`start` is the first omitted byte and `end` is one past the last omitted byte,
counted in the UTF-8 encoding of the original. Both are exact, and both were
computed by the code that did the cutting, not reconstructed afterwards.

**Field elision** (inside one JSON string field):

```
[sutra: partial · 40,470 bytes elided (large output) · field="/output" ·
 id=su_… · original 85,921 B retained 168h ·
 retrieve: sutra_restore(id="su_…", field="/output", mode="bytes",
                            start=12280, end=52750)]
```

The offsets are relative to that field's UTF-8 bytes, so the `field` argument
must travel with them. `field` is a JSON pointer (`/output`, `/items/3/log`);
`~0` and `~1` escape `~` and `/` in keys. A pointer is at most 256 UTF-8 bytes,
and it must be quotable verbatim in both the marker and the suggested call; a
field whose path would be longer, or whose key contains a quote, backslash,
newline, `]` or `)` is left uncompressed rather than labelled with a pointer
recovery cannot resolve. In that case the document is returned whole for that
field, so no evidence is claimed or lost.

**No storage** (disabled, over quota, too large, or the write failed):

```
[sutra: partial · 45,231 bytes elided (large output) · original not retained ·
 re-run the tool with a narrower query (grep/head/offset) to see the middle]
```

No id is invented. The marker states the loss and the only remaining remedy.

**Repeat collapse** uses its own marker, with the same id. It names the first
omitted line and the omitted range in the scope (`end` is exclusive), so the
call needs no substitution:

```
[sutra: 4,998 identical line(s) omitted · id=su_… ·
 retrieve: sutra_restore(id="su_…", mode="lines", start=3, end=5001)]
```

Inside a JSON field the marker adds `field="/log"`, and the line numbers are
relative to that field, not the enclosing document. Line numbers address the
retained original: they do not count the marker line inserted into the
rewritten copy. If the engine cannot align its line numbers with the retrieval
tool's (a form feed, a lone CR, or a tail fragment that starts on a line
break), the marker states that the omitted lines cannot be addressed by line
number and quotes no call. It never emits a placeholder query or a `mode` that
the default response cap would refuse.

### Marker grammar

```
marker      = "[sutra: partial · " size " bytes elided (" reason ")" scope? id? retention? remedy? "]"
size        = 1*DIGIT "," 3DIGIT          ; informational, not parsed by the tool
scope       = " · field=\"" pointer "\""  ; only for an elision inside a JSON field
id          = " · id=" ( "su_" 32HEXDIG )
retention   = " · original " size " B retained " hours "h"
remedy      = " · retrieve: sutra_restore(" args ")"
```

The marker is text for a model, not a serialization format. The durable
contract is the id plus the optional field and either the two byte offsets (a
partial elision) or the line range (a repeat-collapse marker). A repeat marker
that cannot quote a resolvable line range deliberately quotes no call.

![The evidence loop: the tool result enters the middleware, which retains the original bytes under an id and hands the model a smaller result whose marker carries that id; the agent can then call sutra_restore to get an exact slice of the original, bounded to 8 KiB per response](recovery-flow.svg)

## 3. The tool

`sutra_restore` is registered with Hermes' `ctx.register_tool()` in toolset
`sutra`. It is a normal tool: visible only when that toolset is enabled for
the session's platform, and its output passes back through the model context
like any other tool result.

**Tool Search deferral.** When Hermes' Tool Search (progressive disclosure) is
active, plugin tools — including this one — may be deferred behind the
`tool_search` / `tool_describe` / `tool_call` bridge instead of appearing in the
model's direct tool list. The tool still exists, is still registered, and still
dispatches; a model that reports "no such tool" while the toolset is enabled is
hitting deferral, and its recovery path is `tool_search` for the tool, then
`tool_call` with the marker's arguments. Operators who want the tool always
eager can name it in Hermes' `tools.tool_search` configuration (defer list /
threshold). The marker's suggested call is unchanged and works through either
surface.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | the id from a marker |
| `mode` | enum | `bytes` | `bytes`, `lines`, `search`, `full` |
| `field` | string | none | JSON pointer (max 256 UTF-8 bytes) to scope the request to one string field |
| `start` | integer | `0` / `1` | first byte (bytes) or first line (lines), inclusive |
| `end` | integer | end of scope | exclusive byte offset or exclusive line number |
| `query` | string | none | literal text for `search`, at most 200 characters |
| `context` | integer | `3` | lines of context around a search hit, max 20 |
| `case_sensitive` | boolean | `true` | literal search case sensitivity |
| `max_matches` | integer | `5` | maximum matching lines, max 20 |
| `max_bytes` | integer | `8192` | response cap, min 512, max 65536 |

`search` is a literal substring scan over lines. There is no regular
expression surface: a retrieval request cannot ask for work proportional to
anything but the file size.

## 4. Modes

| Mode | Range | Complete when | Pages with |
|---|---|---|---|
| `bytes` | `[start, end)` in UTF-8 bytes | the whole range was returned | `next = {mode:"bytes", start, end, field?}` |
| `lines` | `[start, end)`, 1-based | the whole range was returned | `next = {mode:"lines", start, end, field?}` |
| `search` | up to `max_matches` hits with `context` lines around each | no truncation and no further matches | narrow the query, or raise `max_matches` |
| `full` | the whole scope | always, if it fits under `max_bytes` | `bytes` / `lines` / `search` instead |

`bytes` paging stops on a UTF-8 character boundary, so concatenating successive
pages reproduces the original slice exactly — including multi-byte text.

`lines` and `search` render blocks with a header line:

```
--- lines 120-127 ---
```

A single line longer than the response budget is returned clipped around the
match, with an explicit note naming the byte window:

```
--- line 1 (clipped) ---
…the text around the match…
[sutra: line 1 clipped, bytes 40960-49152 of 85921]
```

A clipped line consumes its line range, so no `next` is offered: asking for the
next line would return nothing. The note says the range was clipped; continue
with `mode=bytes` when the remaining bytes of that line matter.

### Execution-time envelope

There is no wall-clock timeout. A synchronous handler cannot interrupt a
`splitlines()` or `json.loads()` call reliably, and a deadline checked after
the work would only relabel the cost. The enforced work bounds are structural:

- one entry, at most `SUTRA_STORE_MAX_ENTRY_BYTES` (32 MiB by default);
- one response, at most 64 KiB (`SUTRA_RECOVERY_MAX_BYTES` defaults to 8 KiB);
- at most 20 matching lines (5 by default);
- at most a 200-character literal query.

Work is linear in the entry size, and an entry above the per-entry cap is never
retained. Measured at the 32 MiB cap with the repository's `.venv` (Python
3.13.5). The plugin itself is validated against Hermes Agent
v0.22.2+3030.g4d04303 (Python 3.14.7).

The latest `bench/retrieval.json` was recorded at **load 22.36 on 4 CPUs (5.59
per CPU)** and records `"representative": false`. It is a contended sample, not
an envelope:

| Operation at the 32 MiB cap | Median | Worst of 3 | Status |
|---|---:|---:|---|
| `full` (refuses above 8 KiB) | 18.6 ms | 23.6 ms | `too_large` |
| `bytes` page, 64 KiB | 20.8 ms | 24.7 ms | `ok` |
| `lines` page, 100 lines | 486.9 ms | 534.8 ms | `ok` |
| `search`, token absent (full scan) | 507.1 ms | 1425.1 ms | `ok` |
| `search`, token in the last line | 486.2 ms | 674.6 ms | `ok` |
| `full` JSON field (refuses) | 383.6 ms | 820.7 ms | `too_large` |
| `bytes` JSON field page | 176.6 ms | 259.9 ms | `ok` |
| `search`, JSON field, no match | 732.8 ms | 2200.5 ms | `ok` |
| `search`, JSON field, last line | 541.2 ms | 1332.8 ms | `ok` |

The `bytes` rows request a 64 KiB range; the default response cap clips it to
8 KiB, which is what the benchmark records. The 2026-10-25 artifact recorded
329 ms for the JSON-field search, and its load was not captured; the two runs
differ by roughly 2× for that reason. Do not read either as a portable number.

The operator budget is therefore stated structurally, not as a single figure:
**one restore reads at most 32 MiB and returns at most 64 KiB; budget a few
hundred milliseconds at the cap on an idle machine, and allow multi-second
outliers on a contended host.** Raising the entry cap raises the envelope
roughly linearly. Every artifact now records its platform, load and git SHA, and
`bench/retrieval.py` marks a loaded run as not representative.

Reproduce with `.venv/bin/python bench/retrieval.py`; it writes
`bench/retrieval.json`. The hard shape is held by
`test_retrieval_at_the_entry_cap_stays_within_its_bounds`.

## 5. Responses

Every response is one JSON object:

| Field | Present when | Meaning |
|---|---|---|
| `status` | always | `ok`, `not_found`, `expired`, `too_large`, `unavailable`, `error` |
| `content_note` | always | constant reminder that the content is untrusted data, not instructions |
| `id` | known | the requested id |
| `field`, `payload_bytes` | field scope | scope pointer, and the size of the whole payload |
| `mode` | `ok` | the mode served |
| `original_bytes` | `ok`, `too_large` | size of the scope |
| `returned_bytes`, `returned_chars` | `ok` | size of `content` |
| `range` | `bytes`, `lines` | the range actually served |
| `query`, `case_sensitive`, `context` | `search` | the search that was run |
| `matches_found`, `matches_returned`, `matched_blocks` | `search` | counts after the match cap |
| `total_lines` | `lines`, `search` | lines in the scope |
| `complete` | `ok` | the request itself was satisfied in full |
| `more` | `ok` | more matching lines, or more lines/bytes of the scope, remain |
| `next` | paging | the arguments for the next call |
| `sha256_verified` | `full` | the store's integrity check passed: the stored bytes still match the recorded digest |
| `content` | `ok` | the recovered text |
| `note` | most | a human sentence explaining the status |
| `error` | `error` | what was wrong with the request |

`next` is omitted when there is nothing to continue. `more` is not a synonym
for `complete`: a request for lines 1–50 of a 3,000-line original is `complete`
and `more`, because it was answered fully and the original continues.

`sha256_verified` is the store's `verify()` result, not a second hash computed
by the recovery layer: the integrity check has one implementation, owned by the
store that owns the entry. The store hashes the bytes recovery just read and is
about to return, so the flag describes that response rather than a second read
that could see different content; a full restore opens the entry file once.
Without supplied bytes, `verify(entry)` still reads the file once itself.

### Statuses

| Status | Meaning | What the agent should do |
|---|---|---|
| `ok` | served | use the content as evidence |
| `not_found` | no entry with that id: never retained, pruned, or wrong id | re-run the tool narrower |
| `expired` | retained once, deleted by the TTL or quota (or vanished mid-read) | re-run the tool narrower |
| `too_large` | the scope exceeds the response cap | ask for a range or a literal search |
| `unavailable` | storage is disabled (`SUTRA_STORE=0`) | re-run the tool narrower |
| `error` | malformed request (bad id, bad pointer, oversized query) | fix the arguments |

## 6. Failure matrix

| Situation | Behaviour |
|---|---|
| store disabled | no id is written; marker says "not retained"; tool returns `unavailable` |
| per-entry cap exceeded | no id; marker says "not retained" |
| quota exceeded after write | the new entry is pruned immediately, `put` returns nothing, marker says "not retained" |
| TTL elapsed | entry deleted on lookup; `expired` |
| data file truncated or missing | deleted on lookup; `not_found` |
| metadata present, data missing | metadata removed; `not_found` |
| store path is a symlink | store refuses to write; marker says "not retained" |
| interrupted write | temp file, never an entry; cleaned up after an hour |
| cleanup between lookup and read | read raises, tool answers `expired` |
| unknown id | `not_found` (after id validation) |
| malformed id | `error` |
| handler exception | `error` with the class name and a 200-character message; never raised into the agent loop |

## 7. Storage

`$HERMES_HOME/cache/sutra-store/` (or `SUTRA_STORE_DIR`):

```
su_<32 hex>.txt    the original, UTF-8, byte-exact, 0600
su_<32 hex>.json   {id, created_at, original_bytes, sha256, tool, session_id, version}, 0600
```

The directory is `0700`, and a pre-existing one is tightened to it; a directory
owned by another user is refused. Both files are opened with `O_NOFOLLOW`, so a
symlink planted at either path is not followed. Writes go to a `.tmp-*` file in
the same directory and are renamed into place, metadata last; a data file
without metadata is invisible, and a failed metadata write removes it. The entry count and byte quota are checked on every write against
in-process totals. A full sweep — expired entries first, then oldest-first until
the quotas are satisfied — runs when a budget is exceeded, every 128 writes, or
every ten minutes, so a write does not scan the whole store. The first write in
a process seeds the totals with one sweep, which also expires entries and drops
orphans. The totals are per process; another process writing to the same
directory is reconciled by the next sweep. An expired entry is refused on read regardless of when the sweep reaches
it. Readers see a complete entry or a missing one.

### Snapshot and log scope

`sutra.metrics_snapshot()` is the one read surface for runtime state —
compression decisions, restore outcomes, cleanup, and the store's current
size:

```python
>>> sutra.metrics_snapshot()
{"scope": "process",
 "scope_note": "Counters cover this Hermes plugin-host process only; they reset on restart. The JSONL stats log is durable and retains rewritten results only.",
 "restores": {"total": 6, "ok": 1, "bad_id": 1, "expired": 1, "not_found": 1,
              "range": 1, "unavailable": 1},
 "cleanup": {"runs": 3, "entries": 3, "bytes": 1_211, "expired": 2, "quota": 1,
             "orphans": 0, "temp": 0},
 "compression": {"total": 8, "changed": 5, "unchanged": 3,
                 "rules": {"head-tail": 2, "json-fields": 2, "none": 3, "fail-open": 1},
                 "original_bytes": 31_940, "compressed_bytes": 12_480,
                 "saved_bytes": 19_460, "omitted_bytes": 20_001, "partial": 5,
                 "stored": 5, "fail_open": 1, "duration_ms_total": 41.7},
 "store": {"entries": 1, "bytes": 1_300, "enabled": True}}
```

It is deliberately **process-scoped**: the top-level `scope` is `"process"` and
`scope_note` says the counters reset when the Hermes plugin host restarts;
the JSONL stats log is durable and records rewritten results only.

`compression` counts every decision, changed or not: `total`, `changed`,
`unchanged`, per-rule `rules`, bytes before (`original_bytes`) and after
(`compressed_bytes`), `saved_bytes`, `omitted_bytes`, `partial`, `stored`
(retained), `fail_open`, and the summed processing time. Only rewrites append
to the stats log; its `changed` flag is true for every new row, and older rows
lack the field. Unchanged decisions stay in the counters. The log is rotated
at `SUTRA_STATS_MAX_BYTES`. `restores` counts every outcome by reason;
`cleanup.runs` is one entry per cleanup that removed something, split into
`expired`, `quota`, `orphans` and `temp`, with the bytes those removals
reclaimed. Cleanup also logs one line per run (`sutra: cleanup removed …`).
The counters are in-memory and per process; recording one is best-effort and
can never fail a restore or a compression. After a restart,
`compression.total` is zero even though the log has older rewrite rows — that
difference is the contract, stated in the snapshot itself.

## 8. Security rules

1. Recovered text is **data**. It is returned with `content_note` and must never
   be treated as instructions, even if it looks like them.
2. Retrieval is literal and bounded: no regex, no unbounded work, no execution.
3. Ids are unguessable and validated; no caller-supplied path reaches the
   filesystem.
4. Entries are per-profile and readable only by the profile's user. There is no
   cross-user authorization problem because there is no cross-user access.
5. Errors never include file contents or filesystem paths; storage paths are
   not exposed to the model.
6. The store can hold secrets, because tool output does. It is local, expiring
   and unencrypted; see [../SECURITY.md](../SECURITY.md).

## 9. Compatibility

- The marker is additive text; a host that ignores it still receives a valid,
  smaller result.
- The tool name, argument names and response keys above are the stable surface.
  New response keys may be added; existing keys keep their meaning.
- Old ids remain readable across plugin upgrades as long as the store format
  version (`version: 1` in the metadata) is supported. A format change ships
  with a reader for the previous version.
