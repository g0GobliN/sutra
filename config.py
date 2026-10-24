"""The one owner of SUTRA's configuration.

Before this module four places read the environment independently: the engine
(``SUTRA_MAX_BYTES`` and the head/tail budgets), the store (``SUTRA_STORE*``),
recovery (``SUTRA_RECOVERY*``) and the adapter (``SUTRA_ENABLED`` and the stats
file). Nothing could answer "what is this plugin's configuration?", and the four
could silently disagree.

``resolve()`` is now the only reader. Each consumer derives its own view from
the returned ``Config`` — ``engine.Limits``, ``store.StoreConfig``, the recovery
bounds — so one parse feeds all of them. Resolution happens per call rather than
at import: the host sets the environment before the plugin is used, and callers
(tests, operators) may change it between calls. The cost is a dozen dictionary
lookups.

Every name, default and precedence rule is unchanged. Parsing an invalid value
logs one warning per problem per process and falls back to the documented
default.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 24_576
DEFAULT_HEAD_BYTES = 12_288
DEFAULT_TAIL_BYTES = 6_144
DEFAULT_JSON_ITEMS = 0  # retired; parsed only so the warning can fire
DEFAULT_STATS_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_STORE_TTL_HOURS = 168  # 7 days
DEFAULT_STORE_MAX_BYTES = 128 * 1024 * 1024
DEFAULT_STORE_MAX_ENTRY_BYTES = 32 * 1024 * 1024
DEFAULT_STORE_MAX_ENTRIES = 2_000
DEFAULT_RECOVERY_MAX_BYTES = 8_192
DEFAULT_RECOVERY_MAX_MATCHES = 5
RECOVERY_MAX_BYTES_FLOOR = 512
HARD_RECOVERY_MAX_BYTES = 65_536
HARD_RECOVERY_MAX_MATCHES = 20

# One contract for the JSON pointer quoted in a marker, shared by the producer
# (engine) and the consumer (recovery). Measured in UTF-8 bytes: it also bounds
# how much of the marker budget one field path can consume. A field whose
# pointer would exceed it is not elided, because a lossy pointer is worse than
# none — the agent would be told to ask for bytes at the wrong place.
MAX_FIELD_POINTER = 256

_FALSEY = frozenset({"0", "false", "off", "no"})
_warned: set[str] = set()


def _warn_once(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    logger.warning("sutra: %s", message)


def _int(name: str, default: int, *, minimum: int | None = 1,
         maximum: int | None = None) -> int:
    """Read an int from the environment; invalid or out-of-range falls back."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        _warn_once(f"{name}={raw!r} is not an integer; using {default}")
        return default
    if minimum is not None and value < minimum:
        _warn_once(f"{name}={value} is below the minimum {minimum}; using {default}")
        return default
    if maximum is not None and value > maximum:
        _warn_once(f"{name}={value} is above the maximum {maximum}; using {default}")
        return default
    return value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in _FALSEY


def hermes_home() -> str:
    """The active Hermes home; the plugin host sets ``HERMES_HOME`` per profile."""
    return os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes")


def cache_dir() -> str:
    return os.path.join(hermes_home(), "cache")


@dataclass(frozen=True)
class Config:
    """Every resolved setting, in one inspectable object."""

    enabled: bool
    max_bytes: int
    head_bytes: int
    tail_bytes: int
    json_items: int
    stats_path: str
    stats_max_bytes: int
    store_dir: str
    store_enabled: bool
    store_ttl_hours: int
    store_max_bytes: int
    store_max_entry_bytes: int
    store_max_entries: int
    recovery_max_bytes: int
    recovery_max_matches: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve() -> Config:
    """Parse the environment once and return the whole configuration."""
    cache = cache_dir()
    return Config(
        enabled=_flag("SUTRA_ENABLED", True),
        max_bytes=_int("SUTRA_MAX_BYTES", DEFAULT_MAX_BYTES),
        head_bytes=_int("SUTRA_HEAD_BYTES", DEFAULT_HEAD_BYTES, minimum=0),
        tail_bytes=_int("SUTRA_TAIL_BYTES", DEFAULT_TAIL_BYTES, minimum=0),
        json_items=_int("SUTRA_JSON_ITEMS", DEFAULT_JSON_ITEMS, minimum=0),
        stats_path=os.path.abspath(os.path.expanduser(
            os.environ.get("SUTRA_STATS_FILE")
            or os.path.join(cache, "sutra-stats.jsonl"))),
        # Any value is accepted here so 0 or a negative still disables
        # rotation, which is how the adapter has always read this one.
        stats_max_bytes=_int("SUTRA_STATS_MAX_BYTES", DEFAULT_STATS_MAX_BYTES,
                             minimum=None),
        store_dir=os.path.expanduser(
            os.environ.get("SUTRA_STORE_DIR")
            or os.path.join(cache, "sutra-store")),
        store_enabled=_flag("SUTRA_STORE", True),
        store_ttl_hours=_int("SUTRA_STORE_TTL_HOURS", DEFAULT_STORE_TTL_HOURS),
        store_max_bytes=_int("SUTRA_STORE_MAX_BYTES", DEFAULT_STORE_MAX_BYTES,
                             minimum=1024),
        store_max_entry_bytes=_int("SUTRA_STORE_MAX_ENTRY_BYTES",
                                   DEFAULT_STORE_MAX_ENTRY_BYTES, minimum=1024),
        store_max_entries=_int("SUTRA_STORE_MAX_ENTRIES", DEFAULT_STORE_MAX_ENTRIES),
        recovery_max_bytes=_int("SUTRA_RECOVERY_MAX_BYTES",
                                DEFAULT_RECOVERY_MAX_BYTES,
                                minimum=RECOVERY_MAX_BYTES_FLOOR,
                                maximum=HARD_RECOVERY_MAX_BYTES),
        recovery_max_matches=_int("SUTRA_RECOVERY_MAX_MATCHES",
                                  DEFAULT_RECOVERY_MAX_MATCHES,
                                  minimum=1, maximum=HARD_RECOVERY_MAX_MATCHES),
    )
