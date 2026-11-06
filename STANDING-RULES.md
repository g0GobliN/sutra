# Standing rules

## Agent-operated releases

Agents may execute authorized release operations themselves using credentials
stored locally in the system keyring (service `pypi`, username `__token__`),
enrolled once with `python3 scripts/enroll_pypi_credential.py`. A locally stored
credential is an authorization the agent may act on; release work does not
require a separate human round-trip for every command.

## Credential confidentiality is not credential inaccessibility

The PyPI token must never be printed, logged, sent over chat, committed, or
returned in a transcript -- but "secret" does not mean "unusable". An agent
authorized to release may read the token from the keyring and use it directly
to publish, install from an index, or run release checks. Protecting the value
and withholding it from the agent who is authorized to use it are different
things.

## Do not return commands the agent can run

When an agent can perform an operation itself -- build, verify, upload,
publish, status and checksum checks -- using locally stored credentials, it
performs the operation rather than returning a terminal command for the user
to execute and report back.

## Escalate only on genuine blocks

Ask for user intervention only when genuinely blocked by one of:

- missing authorization (a protected environment approval, a Trusted
  Publisher registration, or maintainer rights the agent does not hold),
- unavailable credentials (the keyring is locked, enrollment is missing, or
  the keyring backend fails), or
- an irreversible decision (tagging a version, publishing to production PyPI,
  deleting a release).

Everything else -- verification, builds, rehearsals, checksum comparisons --
proceeds without a human in the loop.
