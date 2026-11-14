"""`sutra update` — compare the installed release with the latest on PyPI.

The CLI owns the wording; this module owns the facts: which version is
installed, which version is published, and how to upgrade this interpreter's
environment. Nothing here runs on import, so a plain ``sutra install`` never
touches the network.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata
from urllib.request import urlopen

PACKAGE = "sutra-agent"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
TIMEOUT_SECONDS = 6.0


def installed_version() -> str:
    """The version installed in the running interpreter."""
    return metadata.version(PACKAGE)


def latest_version(timeout: float = TIMEOUT_SECONDS) -> str:
    """The newest release published on PyPI."""
    with urlopen(PYPI_URL, timeout=timeout) as response:
        payload = json.load(response)
    version = (payload.get("info") or {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"PyPI returned no version for {PACKAGE}")
    return version.strip()


def _release(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", version)[:3])
    return parts or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a later release than ``current``."""
    return _release(candidate) > _release(current)


def is_source_checkout() -> bool:
    """True when pip installed this distribution from a local checkout.

    An editable (or otherwise file-sourced) install cannot be upgraded with
    pip without discarding the checkout, so ``sutra update --apply`` refuses.
    """
    try:
        distribution = metadata.distribution(PACKAGE)
    except metadata.PackageNotFoundError:
        return False
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    # PEP 610: a directory install (editable or plain) records ``dir_info``;
    # a wheel or sdist install records ``archive_info``. Only the former is a
    # checkout the user has to update with git.
    return isinstance(data.get("dir_info"), dict)


def upgrade() -> int:
    """Upgrade this interpreter's environment; return pip's exit status."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE])
    return result.returncode
