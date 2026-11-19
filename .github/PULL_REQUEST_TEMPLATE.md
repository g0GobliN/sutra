## Summary

<!-- What does this change, in one or two sentences? -->

## Motivation

<!-- Why is it necessary? Link the issue or the failing case. -->

## Changes

<!-- The main modifications. One rule per PR: a new compression rule, a fix to
one, a recovery change, or documentation. -->

## Recoverability

<!-- Delete this section when the change cannot affect stored output. -->

Marker text the model sees:

```
<!-- paste it here, with the byte count -->
```

`sutra_restore` call that gets the elided content back:

```
<!-- id, mode, response status -->
```

| | before | after |
|---|---|---|
| fixture | | |

From `bench/benchmark.py`; paste the fixture alongside if it is new.

## Testing

- [ ] Tests added or updated for the new behavior, and the test fails on the old behavior
- [ ] `python -m pytest -q` passes
- [ ] `ruff check .` passes
- [ ] `hermes plugins validate .` passes (if Hermes is installed)
- [ ] `python -m build` and `python scripts/verify_dist.py dist` pass (if packaging changed)

## Compatibility

- [ ] No breaking change: existing stored output and configuration keep working
- [ ] Breaking changes are explained above, with the migration path

## Security

- [ ] No secrets, credentials or private paths were introduced
- [ ] No unsafe permission was introduced in a workflow (`.github/workflows/`)
- [ ] Execution, integration or configuration-handling changes were considered for
      their effect on what a host is allowed to do

## Documentation

- [ ] Documentation was updated where the behavior is user-visible
- [ ] No documentation change is needed

## Checklist

- [ ] The change follows the conventions in `CONTRIBUTING.md`; no rule there was relaxed
- [ ] The change is scoped to one concern and contains no unrelated modification
- [ ] No new dependency was added (SUTRA installs with none)
- [ ] The pull request title describes the change, not the process
