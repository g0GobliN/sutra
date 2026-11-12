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

Every existing name, default and precedence rule is unchanged. Parsing an
invalid value logs one warning per problem per process and falls back to the
documented default.
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

# Agent-policy layer: independent of compression. ``off`` is exactly the
# pre-0.5 prompt behaviour and stays the default until a live run shows the
# guidance earns its per-turn prompt cost; ``balanced`` is the recommended mode
# when it is enabled, and ``aggressive`` is never selected automatically.
DEFAULT_POLICY_MODE = "off"
POLICY_MODES = ("off", "balanced", "aggressive")
# The smallest output worth remembering for exact-duplicate reuse. Below this a
# marker costs about as much context as the repeat it would replace.
DEFAULT_REUSE_MIN_BYTES = 4096

# Advisory token ceiling per result. ``None`` means no token budget. The count
# is an estimate (byte-based unless tiktoken is importable); it sizes the byte
# budget and never invents exact token counts. An unreadable or negative value
# disables the token budget with one warning per process.
DEFAULT_MAX_TOKENS: int | None = None
DEFAULT_TOKENIZER = "byte-estimate"
TOKENIZERS = ("tiktoken", "byte-estimate")

# OpenTelemetry telemetry: off by default so imports and exports are zero-cost
# when not used. ``SUTRA_TELEMETRY_ENDPOINT`` is the OTLP HTTP endpoint (e.g.
# ``http://localhost:4318/v1/metrics``). ``SUTRA_TELEMETRY_SAMPLE_RATE`` is a
# float in [0, 1] for trace sampling (metrics are always exported).
DEFAULT_TELEMETRY_ENABLED = False
DEFAULT_TELEMETRY_ENDPOINT = "http://localhost:4318/v1/metrics"
DEFAULT_TELEMETRY_SAMPLE_RATE = 1.0
# One token is at least one byte, so four bytes per token is a safe upper bound
# when converting a token ceiling into a byte budget: a result larger than that
# cannot possibly fit within max_tokens tokens.
BYTES_PER_TOKEN_ESTIMATE = 4

# One contract for the JSON pointer quoted in a marker, shared by the producer
# (engine) and the consumer (recovery). Measured in UTF-8 bytes: it also bounds
# how much of the marker budget one field path can consume. A field whose
# pointer would exceed it is not elided, because a lossy pointer is worse than
# none — the agent would be told to ask for bytes at the wrong place.
MAX_FIELD_POINTER = 256

def _float(name: str, default: float, *, minimum: float | None = None,
         maximum: float | None = None) -> float:
    """Read a float from the environment; invalid or out-of-range falls back."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        _warn_once(f"{name}={raw!r} is not a float; using {default}")
        return default
    if minimum is not None and value < minimum:
        _warn_once(f"{name}={value} is below the minimum {minimum}; using {default}")
        return default
    if maximum is not None and value > maximum:
        _warn_once(f"{name}={value} is above the maximum {maximum}; using {default}")
        return default
    return value


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


_POLICY_ALIASES = {
    "0": "off", "false": "off", "no": "off", "disabled": "off", "none": "off",
    "1": "balanced", "true": "balanced", "on": "balanced",
}


def _policy_mode() -> str:
    """Read ``SUTRA_POLICY``; an unrecognised value warns and uses the default."""
    raw = os.environ.get("SUTRA_POLICY")
    if raw is None or not str(raw).strip():
        return DEFAULT_POLICY_MODE
    value = str(raw).strip().lower()
    value = _POLICY_ALIASES.get(value, value)
    if value not in POLICY_MODES:
        _warn_once(f"SUTRA_POLICY={raw!r} is not one of "
                   f"{', '.join(POLICY_MODES)}; using {DEFAULT_POLICY_MODE}")
        return DEFAULT_POLICY_MODE
    return value


def _optional_int(name: str, default: int | None) -> int | None:
    """Read an int from the environment; invalid or negative falls back."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        _warn_once(f"{name}={raw!r} is not an integer; using {default}")
        return default
    if value < 0:
        _warn_once(f"{name}={value} is negative; {name} disabled")
        return None
    return value


def _tokenizer() -> str:
    """Read ``SUTRA_TOKENIZER``; unrecognised warns and uses the default."""
    raw = os.environ.get("SUTRA_TOKENIZER")
    if raw is None or not str(raw).strip():
        return DEFAULT_TOKENIZER
    value = str(raw).strip().lower()
    if value not in TOKENIZERS:
        _warn_once(f"SUTRA_TOKENIZER={raw!r} is not one of "
                   f"{', '.join(TOKENIZERS)}; using {DEFAULT_TOKENIZER}")
        return DEFAULT_TOKENIZER
    return value


def estimated_tokens(text: str, tokenizer: str | None = None) -> int:
    """Estimate the token count of ``text``.

    ``tiktoken`` (cl100k_base) counts exactly when requested and importable;
    otherwise a byte-based estimate (``ceil(bytes / 4)``) is used. It is an
    estimate: the byte budget it feeds is deliberately conservative, never
    exact, and lone surrogates never break it.
    """
    name = tokenizer or DEFAULT_TOKENIZER
    if name not in TOKENIZERS:
        name = DEFAULT_TOKENIZER
    if name == "tiktoken":
        try:
            import tiktoken  # type: ignore[import-not-found]  # optional: exact counts when present
        except ImportError:
            logger.debug("sutra: tiktoken not installed; byte-based token "
                         "estimate")
            name = "byte-estimate"
    if name == "byte-estimate":
        encoded = text.encode("utf-8", "replace")
        return (len(encoded) + BYTES_PER_TOKEN_ESTIMATE - 1) // BYTES_PER_TOKEN_ESTIMATE
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # lone surrogates and anything else: bytes always work
        logger.debug("sutra: tiktoken encode failed; byte-based token estimate")
        encoded = text.encode("utf-8", "replace")
        return (len(encoded) + BYTES_PER_TOKEN_ESTIMATE - 1) // BYTES_PER_TOKEN_ESTIMATE


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
    policy_mode: str = "off"
    reuse: bool = False
    reuse_min_bytes: int = DEFAULT_REUSE_MIN_BYTES
    max_tokens: int | None = None
    tokenizer: str = DEFAULT_TOKENIZER
    telemetry_enabled: bool = DEFAULT_TELEMETRY_ENABLED
    telemetry_endpoint: str = DEFAULT_TELEMETRY_ENDPOINT
    telemetry_sample_rate: float = DEFAULT_TELEMETRY_SAMPLE_RATE

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve() -> Config:
    """Parse the environment once and return the whole configuration."""
    cache = cache_dir()
    enabled = _flag("SUTRA_ENABLED", True)
    return Config(
        enabled=enabled,
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
        # Disabling the plugin disables its prompt guidance too: reporting a
        # mode that cannot act would make `sutra config` lie.
        policy_mode=_policy_mode() if enabled else "off",
        reuse=_flag("SUTRA_REUSE", True),
        reuse_min_bytes=_int("SUTRA_REUSE_MIN_BYTES", DEFAULT_REUSE_MIN_BYTES,
                             minimum=1024),
        max_tokens=_optional_int("SUTRA_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        tokenizer=_tokenizer(),
        telemetry_enabled=_flag("SUTRA_TELEMETRY", DEFAULT_TELEMETRY_ENABLED),
        telemetry_endpoint=os.environ.get("SUTRA_TELEMETRY_ENDPOINT")
        or DEFAULT_TELEMETRY_ENDPOINT,
        telemetry_sample_rate=_float("SUTRA_TELEMETRY_SAMPLE_RATE",
                                     DEFAULT_TELEMETRY_SAMPLE_RATE,
                                     minimum=0.0, maximum=1.0),
    )
