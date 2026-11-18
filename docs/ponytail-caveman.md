# Ponytail and Caveman: what was inspected, and what SUTRA took

`goals.md` PART 6 and PART 7 say not to invent what Caveman and Ponytail do:
investigate them, check their licences, and state what is adopted. This page is
the record of that investigation. Both repositories were cloned and read
directly, at these commits:

| Project | Repository | Commit read | Licence |
|---|---|---|---|
| Ponytail | <https://github.com/DietrichGebert/ponytail> | `96608e0` (2026-10-29) | MIT (`LICENSE`, `skills/ponytail/SKILL.md`) |
| Caveman | <https://github.com/JuliusBrussee/caveman> | `a192894` (2026-11-17) | Apache-2.0 (`LICENSE`, `LICENSE-MIT`, `shrink/LICENSE.launcher`) |

No source was copied. The adopted work is re-implemented from the described
behaviour, with the origin named in the code. Measurements published by those
projects are theirs, on their workloads; they are not repeated here as SUTRA
results.

## Ponytail: a rules layer, with one deterministic tool

The skill is prose, not a transformer. `skills/ponytail/SKILL.md` (52 lines)
tells the agent to read the task and every caller first, then take the smallest
complete change: skip features nobody asked for, reuse an existing helper, use
the standard library, use an installed dependency, and never add one for a few
lines. It explicitly forbids cutting validation at trust boundaries, error
handling that prevents data loss, security, accessibility, or anything the user
asked for, and asks for one small test on non-trivial logic.

That is a prompt-level policy. SUTRA cannot install it from a tool-result hook,
and does not try to.

The deterministic part is `hooks/ponytail-map.js` (111 lines): a codebase map so
"reuse first" does not require a search.

- File list: `git ls-files` with a 64 MiB buffer and 3 s timeout; if that fails,
  a directory walk capped at depth 6 and 4000 files.
- Skips: `node_modules`, `.git`, `dist`, `build`, `out`, `vendor`, `venv`,
  `.venv`, `__pycache__`, `coverage`, `.next`, `target`, `migrations`,
  `versions`, `fixtures`, `tests`, `__tests__`, `spec`; and test, generated and
  minified files by name.
- Extraction: one regex per language for top-level definitions (Python
  `def`/`class`, JavaScript/TypeScript `export …`, Go `func`/`type`, Rust
  `pub fn/struct/enum/trait/type`, Ruby, and the JVM family).
- Output: one line per folder, inside `PONYTAIL_MAP_CHARS` (default 2000), with
  shared folders (`util`, `helper`, `lib`, `common`, `shared`, `service`,
  `component`, `hook`, `core`, `model`, `schema`, `api`) listed first when the
  budget is tight.

**Adopted:** `repomap.py` and `sutra map` implement that idea for this package:
stdlib only, no model, no network, same skip and shared-first rules, same
budgeted one-line-per-folder shape. It is advisory output for a caller; SUTRA
does not use it to decide what may be dropped.

**Not adopted:** the skill rules themselves. They are prompt text for a host
model, not a tool-result transform; SUTRA's guarantees are deterministic and do
not depend on a model following instructions.

## Caveman: a Go engine with sixteen compressors

`engine/compressors/` contains dedicated compressors for JSON, logs, test
reports, code, diffs, search results, tables, HTML, config, tool schemas, TOON,
accessibility trees, terminal output, repetition, line endings, relevance, and
redundancy. The engine stores originals in a local content store (CCR, 512 MiB
default cap), returns the input unchanged when it cannot parse it or when the
output is not smaller, and labels its token counts `inferred` in `o200k_base`
rather than claiming provider bills. Those are the same honesty rules SUTRA
already follows (`docs/contract.md`), so the shape is familiar.

Two techniques were worth taking:

### The distinct-content guard

`engine/compressors/redundancy.go` defines `keepNonRedundant(uint, keep)`. An
elider keeps first/last units and whatever carries a signal; on a payload of
repeated boilerplate that is a real win, but on a payload where every unit is
distinct it silently deletes the answer. The guard forces every distinct kind of
content to survive at least once:

- a dropped unit is elided only when a surviving unit's vocabulary contains at
  least `redundantUnitContainment = 0.9` of that unit's vocabulary;
- seed classes come from at most `redundancyMaxClasses = 64` evenly spread kept
  units, so the check costs the same on 200 and 20 000 lines;
- at most `redundancyMaxPromotions = 128` first-of-kind units are promoted
  before the payload is treated as a document and the rest is kept;
- the function is deterministic and order-preserving, because a compressed
  block that re-serialises differently on the next turn would bust the
  provider's prefix cache.

**Adopted:** `context.py` now applies the same 0.9 vocabulary-containment rule
before dropping a near-duplicate (`NEAR_DUPLICATE_CONTAINMENT`, `_containment`).
A SimHash-close item that carries vocabulary the representative lacks is kept
and stays available to the budget selection. The test
`test_near_duplicate_with_distinct_vocabulary_is_kept` forces one group with
`near_distance=64` so the guard, not the bit distance, is what decides.

**Not adopted:** the unit-class bookkeeping and the promotion cap. SUTRA's
engine elides whole lines and JSON items with explicit markers and a recovery
id; it does not rewrite documents unit by unit, so the class machinery would
guard a transform that does not exist here.

### Repetition collapsing

`engine/compressors/repetition.go` collapses runs of at least three identical
adjacent non-blank lines into one line plus a `… caveman: N identical lines
elided …` marker. It is idempotent (a marker line is never collapsed again) and
only applied when the result is smaller.

SUTRA already has this transform: `engine.collapse_repeats` (engine.py:397)
collapses identical runs, keeps two occurrences instead of one, emits a
`sutra: N identical line(s) omitted` marker with the recovery id, and the
recovered original is byte-exact. Nothing changed here; the difference is the
keep count (2 vs 1) and the marker format.

### Compressors SUTRA deliberately does not have

- **Diffs.** SUTRA leaves unified diffs byte-identical (engine.py:45): a changed
  diff is not a smaller diff, it is a different patch, and an agent that applies
  it can corrupt a working tree.
- **Code listings.** SUTRA has no parser for 20 languages. A regex or a
  tree-sitter dependency would be a new correctness surface for a gain that
  code-shaped results do not reliably show; the protected-span validator
  (`preserve.py`) covers identifiers, paths and commands in whatever text it
  does rewrite.
- **TOON and accessibility trees.** Out of scope for a tool-result hook; no
  measured demand in this repository's fixtures.
- **Provider prefix-cache shaping.** Caveman's line-ending and determinism work
  is motivated by prefix caches. SUTRA's transforms are deterministic too, but
  the package does not observe a provider cache; `docs/cache.md` says so
  explicitly instead of implying otherwise.

## What this investigation changes in the claim

The README comparison table already separates the three projects by focus and
states that their published numbers are their own. After this pass:

- SUTRA can point to a concrete reuse-first lookup surface (`sutra map`) instead
  of only describing the idea.
- The near-duplicate path has a second, vocabulary-level guard taken from
  Caveman's engine, with a test that isolates it.
- No claim of compatibility, shared code, or shared benchmark result is made.

## Reproducing the inspection

```console
git clone --depth 1 https://github.com/DietrichGebert/ponytail.git
git clone --depth 1 https://github.com/JuliusBrussee/caveman.git
sed -n '1,160p' ponytail/skills/ponytail/SKILL.md
sed -n '1,120p' ponytail/hooks/ponytail-map.js
sed -n '1,120p' caveman/engine/compressors/redundancy.go
sed -n '1,80p' caveman/engine/compressors/repetition.go
```

The clones are read-only reference material; neither project is a dependency of
SUTRA and neither is vendored.
