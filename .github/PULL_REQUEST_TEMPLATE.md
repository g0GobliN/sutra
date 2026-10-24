## What this changes

<!-- One rule per PR: a new compression rule, a fix to one, a recovery change, or docs. -->

## Why

<!-- The shape that is currently mishandled. Link the issue if there is one. -->

## Proof it is recoverable

Marker text the model sees:

```
<!-- paste it here, with the byte count -->
```

`sutra_restore` call that gets the elided content back:

```
<!-- id, mode, response status -->
```

## Byte numbers

| | before | after |
|---|---|---|
| fixture | | |

From `bench/benchmark.py` (paste the fixture alongside if it is new).

## Checks

- [ ] `python -m pytest -q` passes
- [ ] `hermes plugins validate .` passes (if Hermes is installed)
- [ ] The test was written first and fails on the old behaviour
- [ ] No new dependencies
- [ ] No rule in `CONTRIBUTING.md` was relaxed
