#!/usr/bin/env python3
"""Securely enroll a PyPI API token into the local keyring, once.

Run this once on the local machine. It reads the token via getpass (no echo)
and stores it under service 'pypi', username '__token__', so release tooling
can read it back with keyring.get_password("pypi", "__token__") without the
token ever appearing in ~/.pypirc, environment variables, source code, or Git.

The token is never echoed, printed, or logged, and no copy of it is left on
disk.

Usage:
    python3 scripts/enroll_pypi_credential.py
"""

from __future__ import annotations

import getpass
import os
import pathlib
import sys

try:
    import keyring
except ImportError:
    print(
        "ERROR: keyring module not available. Install it: pip install keyring",
        file=sys.stderr,
    )
    sys.exit(1)
from keyring.errors import KeyringError

SERVICE = "pypi"
USERNAME = "__token__"
PYPIRC = pathlib.Path.home() / ".pypirc"
PYPIRC_MODE = 0o600  # owner read/write only

# Environment variables that sometimes carry a long-lived PyPI token. Only
# their presence is flagged -- never their values.
TOKEN_ENV_VARS = ("PYPI_TOKEN", "PYPI_API_TOKEN", "TWINE_PASSWORD", "TWINE_API_KEY")


def _restrictive(path: pathlib.Path) -> None:
    """Make ``path`` owner-only if it exists, without otherwise touching it."""
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        return
    if mode != PYPIRC_MODE:
        path.chmod(PYPIRC_MODE)


def _pypirc_hints() -> list[str]:
    """Flag long-lived credentials parked in .pypirc (never their values)."""
    hints: list[str] = []
    if not PYPIRC.exists():
        return hints
    try:
        text = PYPIRC.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"could not read {PYPIRC}: {exc}"]
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(("password =", "token =", "apikey =", "api_key =")):
            hints.append(f"{PYPIRC} has a {stripped.split(' =', 1)[0]} entry")
    return hints


def _leaked_env() -> list[str]:
    return [name for name in TOKEN_ENV_VARS if name in os.environ]


def main() -> int:
    old_umask = os.umask(0o077)
    try:
        token = getpass.getpass("PyPI API token (input hidden): ")
    except (EOFError, KeyboardInterrupt):
        print("\nEnrollment aborted: no token provided", file=sys.stderr)
        return 1
    finally:
        os.umask(old_umask)

    if not token or not token.strip():
        print("ERROR: empty token", file=sys.stderr)
        return 1
    token = token.strip()
    if not token.startswith("pypi-"):
        print(
            "WARNING: token does not start with 'pypi-'; storing anyway.",
            file=sys.stderr,
        )

    try:
        keyring.set_password(SERVICE, USERNAME, token)
    except KeyringError as exc:
        print(
            f"ERROR: the keyring would not accept the token: {exc}",
            file=sys.stderr,
        )
        return 1

    # Verify it was stored without revealing the value, then scrub our
    # in-memory copy. The token is never printed or logged at any point.
    stored = keyring.get_password(SERVICE, USERNAME)
    if stored != token:
        print("ERROR: credential storage verification failed", file=sys.stderr)
        return 1
    token = None

    # Any config the enrollment touches is left owner-only.
    _restrictive(PYPIRC)
    notes = _pypirc_hints() + [
        f"{name} is set in the environment" for name in _leaked_env()
    ]
    if notes:
        print("Enrolled, with configuration notes:", file=sys.stderr)
        for note in notes:
            print(f"  - {note}", file=sys.stderr)
        print(
            "  remove long-lived credentials from .pypirc and unset the "
            "environment variables",
            file=sys.stderr,
        )
    else:
        print(
            f"OK: PyPI credential stored in local keyring "
            f"(service='{SERVICE}', user='{USERNAME}')",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
