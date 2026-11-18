# Redundancy classification

`redundancy.py` answers the question that exact-output reuse cannot: when a
host is about to repeat work — the same read, the same command, the same
inspection — is that repetition safe to skip, does it need revalidation, or
must it run no matter what?

The module never decides on its own that work may be skipped. It returns one
of four classifications with a reason, and the caller stays responsible.

## The four answers

| Classification | Meaning | What a caller may do |
|---|---|---|
| `SAFE_TO_REUSE` | The same call was recorded and every recorded dependency (file hash, size, mtime, version stamp) is unchanged, and the record has not expired. | Skip the call and reuse the recorded result fingerprint. |
| `REQUIRES_REVALIDATION` | A record exists, but a dependency changed or the record expired. | Re-check the cheap question (hash, version, timestamp) before deciding. |
| `MUST_EXECUTE` | The command looks like a test, lint, type check, security scan or verification step. | Run it. Always. |
| `UNKNOWN` | No record, or nothing to compare. | Run it, or record it for next time. |

`MUST_EXECUTE` wins over a perfectly fresh record. Mandatory verification is
never skipped by an optimization.

## Mandatory commands

A command is mandatory when any whitespace/shell-separated token is one of:

```
test tests pytest unittest tox nox jest vitest mocha rspec phpunit ctest
audit auditing security sast dast scan scanner lint linting typecheck
type-check mypy tsc ruff flake8 pylint eslint bandit semgrep trivy grype
pip-audit verify check
```

Matching is per token after lowercasing, so `git checkout main` is **not**
mandatory (its token is `checkout`) while `make check` and `npm audit` are.
The list is deliberately broad: a false `MUST_EXECUTE` costs one command, a
false skip can cost correctness.

## Dependencies and snapshots

`snapshot(paths, root=".", versions=None)` captures one string per file:

```
<size>:<mtime_ns>:<sha256>      # files up to 1 MiB
<size>:<mtime_ns>:unhashed      # larger files: metadata only
missing | unreadable            # reported, never assumed
```

`versions` carries whatever the host can supply (`tool --version`, model id,
config fingerprint). A snapshot's `digest()` is a SHA-256 over the sorted JSON
of files and versions, so two snapshots are compared structurally and the
ledger stays small.

## The ledger

One JSON file, written atomically (temporary file plus `os.replace`):

```json
{
  "version": 1,
  "entries": {
    "<call key>": {
      "created": 1730000000.0,
      "ttl": 3600,
      "result": "<result fingerprint>",
      "files": {"src/app.py": "12345:1730000000123456789:ab…"},
      "versions": {"python": "3.13.1"},
      "digest": "<snapshot digest>"
    }
  }
}
```

- The key is `call_fingerprint(tool, arguments)` — SHA-256 over the canonical
  JSON pair. Nothing about the result's content is stored, only its digest.
- `ttl` is per entry; a negative TTL means it never expires.
- The ledger keeps at most 512 entries (`max_entries`), evicting the oldest.
- A missing, corrupt or wrong-version file is treated as empty. Classification
  then falls back to `UNKNOWN`, which is the safe direction.
- The default path is `~/.sutra/redundancy.json`, overridable with
  `SUTRA_REDUNDANCY_FILE`. Give every user and every machine its own file: the
  ledger has no user identity and must not be shared across users.

## Command line

```console
$ sutra redundancy record --tool Read --arguments '{"path":"src/app.py"}' \
      --dependency src/app.py --result "$(sha256sum src/app.py | cut -d' ' -f1)"
recorded 4f1c2a9e3b7d… with 1 dependency file(s)

$ sutra redundancy check --tool Read --arguments '{"path":"src/app.py"}' \
      --dependency src/app.py
SAFE_TO_REUSE: the call and all recorded dependencies are unchanged

$ sutra redundancy check --command "pytest -q" --key anything
MUST_EXECUTE: 'pytest -q' looks like a verification command; tests, lint,
type checks and security scans are never skipped
```

Exit codes for `check`: `0` only for `SAFE_TO_REUSE`; `1` for every other
classification; `2` for a malformed invocation. `--json` prints the decision as
an object (`classification`, `reason`, `changed`, `reusable`). `record` and
`forget` exit `0`.

Measured overhead on this machine (Python 3.13, 10 kB fixture): 16 µs per
classification and 240 µs per dependency snapshot. Both are far below the cost
of the call they are deciding about, but they are not free, so the check is
opt-in.

## What this does not do

- A PostToolUse-style hook sees results, not tool calls, so SUTRA cannot wire
  this automatically. The classification is available to a host that chooses
  to ask, through the CLI or the library.
- File dependencies are compared by content hash up to 1 MiB, and by size plus
  mtime above that. A same-size edit inside the same nanosecond on a file over
  1 MiB would be missed; that is stated instead of claimed away.
- Commands are classified by their text. A verification step hidden inside a
  script whose name does not look like one (for example `./verify-all`) is
  still caught by the token list only if the token appears; when in doubt, the
  caller should treat the answer as `UNKNOWN` rather than `SAFE_TO_REUSE`.
- There is no cross-user or cross-machine sharing, by design.
