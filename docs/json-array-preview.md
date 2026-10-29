# JSON array previews: evaluated and deferred

**Status:** deferred, 2026-10-29. This decision does not change compression
behavior.

SUTRA already guarantees that a JSON array in is a JSON array out. The old
array-elision mode returned `{"sutra": …, "items": […]}` for an `[…]` input, so
the top-level type changed; that mode was removed and `SUTRA_JSON_ITEMS` is now
retired and ignored.

That left a narrower question. An 8,000-item array of small objects is 206,890
bytes and currently saves nothing, because no string field is large enough to
rewrite. Could a *preview*—keep the first and last few items, add a marker that
names the omission and its recovery id, and keep the top-level array—recover
some of those bytes safely?

This document records the evaluation. The answer is **no for a generic
middleware layer**: a preview can pass the top-level-type check while breaking
the item contract that downstream code relies on.

## What happens today

Measured through `scripts/sutra-cli.py compress` with an isolated `HERMES_HOME`
(no trailing newline in the input):

| Input | Bytes in | Bytes out | Visible copy |
|---|---:|---:|---|
| 8,000 objects `{"id": i, "tag": "x"}` | 206,890 | 206,890 | byte-identical; 8,000 elements, every one an object |
| 8,000 objects, `indent=2` | 318,892 | 318,892 | byte-identical; formatting preserved |
| 4 long strings, 800 lines each | 31,568 | 21,528 | 4 elements, every one still a string; the marker is inside the first |

For the first two, the CLI reports `changed: false` and `id: null`; the
benchmark reports the rule as `json-kept`. For the third, the CLI reports
`changed: true` and an id; `sutra restore --mode full` returns `status: ok`,
`sha256_verified: true`, and the exact original text.

The third row is the safe path that already exists: a long string value can be
rewritten in place, so the element count, element types and object keys stay
exactly the same. The array is not previewed; its strings are compressed.

## The preview candidate, and why it fails

The smallest candidate kept three leading items, two trailing items and one
marker string in the middle:

```python
preview = items[:3] + [
    '[sutra: 7,995 items elided · id=su_example · '
    'retrieve: sutra_restore(id="su_example", mode="full")]'
] + items[-2:]
```

For the 206,890-byte fixture this is **238 bytes**—a 99.9% reduction that:

- still has top-level type `list`;
- still parses with `json.loads`;
- therefore passes the engine's current top-level-type invariant.

It also fails ordinary consumers immediately:

| Consumer assumption | Observed result |
|---|---|
| Every element is an object; read `item["id"]` | `TypeError: string indices must be integers, not 'str'` |
| The array still has 8,000 positions; read `preview[5000]` | `IndexError: list index out of range` |
| Every element has the same key set | 5 objects plus 1 string; the marker has no `"id"` or `"tag"` |
| Append the marker as an object (`{"sutra": …}`) instead | `KeyError: 'id'` on the same consumer |

The first row is the important one: the payload is valid JSON and the top-level
type is correct, but the element contract is gone. A shallow "array in, array
out" guard cannot detect this.

## Why there is no safe generic preview

1. **An array's contract is more than its top-level type.** Length, element
   type, key set, key order (for some consumers) and homogeneity are all part of
   what a caller depends on. Removing elements or replacing one with a marker
   changes at least one of them.
2. **There is no out-of-band metadata channel.** A tool result is one string.
   Anything that names the omission is bytes in that same string, and a generic
   consumer cannot distinguish a marker from data.
3. **Preserving the length is worse.** Filling omitted positions with empty
   objects, `null`, or placeholder values keeps the count but fabricates
   evidence and still changes the schema. That is not a preview; it is a lie
   about the data.
4. **A recovery id only helps a reader that can act on it.** It lets a model or
   a human call `sutra_restore`; it does not repair a program that parsed the
   array and now has six elements instead of 8,000.
5. **An opt-in flag does not make the decision for the consumer.** SUTRA sits
   between a tool and whoever reads its result. It cannot know whether that
   reader is a model, a JSON Schema validator, or code that indexes by position.
   A flag that trades schema for bytes belongs to the tool that owns the schema,
   not to the transport.

## Alternatives considered

- **Field-level compression (shipped).** Rewrite long string values in place.
  This keeps the array's length, element types and keys, and the original bytes
  remain recoverable. It is the only array compression SUTRA performs.
- **Minify pretty-printed JSON (deferred, separate decision).** The 318,892-byte
  pretty fixture would become 206,890 bytes (35.1% smaller) by re-encoding with
  compact separators. That is not a preview and it keeps every value, but it
  changes payloads that are currently returned byte-identically, changes
  formatting, and inherits the existing duplicate-key normalization. It needs
  its own decision and benchmark row; it is not part of this one.
- **Opt-in element elision (deferred).** It needs a host-provided metadata
  channel or a consumer-declared schema that says the shortened array is
  acceptable. Neither exists today.
- **Tool-level pagination (recommended today).** Ask the tool for fewer rows:
  `jq '.[0:50]'`, an API page limit, `head`, an offset, or a filter. The tool
  owns the schema and can make the shortening explicit in its own protocol.

## Revisit conditions

Reconsider a preview only if at least one of these becomes true:

- the host gives the adapter an out-of-band metadata field alongside the tool
  output, so the omission does not have to be an array element;
- a tool adapter declares its item schema and its consumer explicitly accepts a
  shortened or marker-bearing array;
- a marker can be represented without changing the item count, item types or key
  set—which, for an arbitrary array, is not possible today.

Until then, large arrays without a compressible string field are returned
untouched. The 0.0% saving in the benchmark is the deliberate price of keeping
the payload usable by the code that asked for it.

## Reproduction

```bash
python3 - <<'PY' > /tmp/objects.json
import json, sys
sys.stdout.write(json.dumps([{"id": i, "tag": "x"} for i in range(8000)]))
PY
HERMES_HOME="$(mktemp -d)" python3 scripts/sutra-cli.py compress --file /tmp/objects.json
```

The output has `"changed": false`, `"id": null`, and an `"output"` byte-identical
to the input. The long-string case and its exact restore are covered by
`test_json_array_of_long_strings_keeps_every_element` in
`tests/test_cli_adapter.py`; the untouched case is covered by
`test_json_array_of_small_objects_is_returned_byte_identical`.

See [contract.md](contract.md) for the guarantee and [benchmark.md](benchmark.md)
for the fixture's byte and token rows.
