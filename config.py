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

Every existing name and precedence rule is unchanged. The one deliberate
default change is ``SUTRA_MODE``: ``balanced`` now applies when nothing is set,
so a normal installation gets optimization without choosing individual
features. ``SUTRA_MODE=off`` or an explicit legacy ``SUTRA_SEARCH=off`` keeps
the previous no-optimization path. Parsing an invalid value logs one warning per
problem per process and falls back to the documented default.
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
#: Bounded reuse of read-only tool results before the call runs (replay.py).
#: Off in ``off`` mode; a host must also route calls through a pre-dispatch
#: frame, which today only the Hermes middleware does.
DEFAULT_REPLAY_TTL_SECONDS = 600
DEFAULT_REPLAY_MAX_ENTRIES = 64
DEFAULT_REPLAY_MAX_RESULT_BYTES = 256 * 1024
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

# Maximum-effort optimization layer: ``off`` keeps the historical single-pass
# output byte-for-byte. ``balanced`` allows a few cheap formatting passes, and
# ``maximum`` allows more passes and a larger time budget; both are bounded by
# an explicit cap so a pathological payload cannot stall a tool result.
DEFAULT_SEARCH_MODE = "off"
SEARCH_MODES = ("off", "safe", "balanced", "aggressive")
DEFAULT_SEARCH_PASSES = 4
DEFAULT_SEARCH_BUDGET_MS = 50
# Wording compression is a separate opt-in: it changes prose, not formatting,
# so it is never enabled merely by choosing a search mode.
DEFAULT_LANGUAGE = False
# How SROF's overhead term ``O(a)`` is supplied. ``nominal`` (default) keeps
# plan selection independent of host load; ``measured`` charges each plan the
# share of the mode's processing budget it actually used, which lets a plan
# that spends the whole budget for a sliver be refused. Both are dimensionless
# ratios, so neither invents a bytes-to-milliseconds exchange rate.
DEFAULT_SEARCH_OVERHEAD = "nominal"
DEFAULT_PROMPT = False
DEFAULT_PROMPT_KEEP_LAST = 6
DEFAULT_PROMPT_MIN_MESSAGE_BYTES = 8_192
DEFAULT_PROMPT_MIN_REQUEST_BYTES = 65_536
DEFAULT_PROMPT_MAX_MESSAGES = 8
# The request stage may retain a host-produced tool result in the store and
# quote the new id, which is what lets it shorten results SUTRA never saw. The
# write is fail-open: if the store declines, the message is left untouched.
DEFAULT_PROMPT_STORE_HOST = True
# How much of the request stage each mode may do. A deeper mode looks at
# smaller results, touches more of them per request and accepts a lower
# request-size floor; it also keeps a smaller head/tail preview, because a
# result that shows nothing of itself invites the agent to read it again, and
# a second read of the original costs far more than the preview it replaces.
# The numbers are the settings ``tests/test_mode_policy.py`` measures, and they
# are mode *defaults*: an explicit ``SUTRA_PROMPT_*`` variable still wins, so a
# deployment can pin one dimension without giving up the mode's others.
DEFAULT_PROMPT_PREVIEW = True
DEFAULT_PROMPT_PREVIEW_HEAD = 1_536
DEFAULT_PROMPT_PREVIEW_TAIL = 768
PROMPT_CAPS_BY_MODE = {
    "balanced": {"min_message_bytes": DEFAULT_PROMPT_MIN_MESSAGE_BYTES,
                 "min_request_bytes": DEFAULT_PROMPT_MIN_REQUEST_BYTES,
                 "max_messages": DEFAULT_PROMPT_MAX_MESSAGES,
                 "preview": True,
                 "preview_head": DEFAULT_PROMPT_PREVIEW_HEAD,
                 "preview_tail": DEFAULT_PROMPT_PREVIEW_TAIL},
    "aggressive": {"min_message_bytes": 4_096, "min_request_bytes": 32_768,
                   "max_messages": 16, "preview": True,
                   "preview_head": DEFAULT_PROMPT_PREVIEW_HEAD,
                   "preview_tail": DEFAULT_PROMPT_PREVIEW_TAIL},
    "beast": {"min_message_bytes": 2_048, "min_request_bytes": 16_384,
              "max_messages": 32, "preview": True,
              "preview_head": 384, "preview_tail": 192},
}
SEARCH_OVERHEAD_SOURCES = ("nominal", "measured")

# Unified optimization modes. ``SUTRA_MODE`` is the canonical switch; the
# legacy ``SUTRA_SEARCH`` names above remain accepted and keep their old
# semantics when no mode is set. ``balanced`` is the default because selecting
# a mode must be the only decision a normal installation has to make; the
# earlier opt-in default asked users to discover a second switch before any
# optimization happened. Installations that need the previous byte-identical
# output set ``SUTRA_MODE=off`` explicitly.
DEFAULT_MODE = "balanced"
MODES = ("off", "balanced", "aggressive", "beast")
MODE_FILE = os.path.join("~", ".sutra", "mode")

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


_SEARCH_ALIASES = {
    "0": "off", "false": "off", "no": "off", "disabled": "off", "none": "off",
    "1": "balanced", "true": "balanced", "on": "balanced",
    # "safe" is its own conservative mode: formatting only, one pass, and the
    # language strategy is never appended.
    # The goals document calls the deepest mode "aggressive"; "maximum" is the
    # name it had during development and stays accepted.
    "maximum": "aggressive", "max": "aggressive",
}


def _search_overhead() -> str:
    """Read ``SUTRA_SEARCH_OVERHEAD``; unknown values warn and use the default."""
    raw = os.environ.get("SUTRA_SEARCH_OVERHEAD")
    if raw is None or not str(raw).strip():
        return DEFAULT_SEARCH_OVERHEAD
    value = str(raw).strip().lower()
    if value not in SEARCH_OVERHEAD_SOURCES:
        _warn_once(f"SUTRA_SEARCH_OVERHEAD={raw!r} is not one of "
                   f"{', '.join(SEARCH_OVERHEAD_SOURCES)}; "
                   f"using {DEFAULT_SEARCH_OVERHEAD}")
        return DEFAULT_SEARCH_OVERHEAD
    return value


def _search_mode() -> str:
    """Read ``SUTRA_SEARCH``; an unrecognised value warns and uses the default."""
    raw = os.environ.get("SUTRA_SEARCH")
    if raw is None or not str(raw).strip():
        return DEFAULT_SEARCH_MODE
    value = str(raw).strip().lower()
    value = _SEARCH_ALIASES.get(value, value)
    if value not in SEARCH_MODES:
        _warn_once(f"SUTRA_SEARCH={raw!r} is not one of "
                   f"{', '.join(SEARCH_MODES)}; using {DEFAULT_SEARCH_MODE}")
        return DEFAULT_SEARCH_MODE
    return value


_MODE_ALIASES = {
    "0": "off", "false": "off", "no": "off", "disabled": "off", "none": "off",
    "1": "balanced", "true": "balanced", "on": "balanced",
    "safe": "balanced", "maximum": "aggressive", "max": "aggressive",
}


def mode_file_path() -> str:
    """Where `sutra mode <name>` persists the choice."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("SUTRA_MODE_FILE") or MODE_FILE))


def _read_mode_file() -> str:
    path = mode_file_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.readline().strip().lower()
    except OSError:
        return ""
    return _MODE_ALIASES.get(raw, raw)


def _resolve_mode() -> tuple[str, str]:
    """Resolve the unified mode and where it came from.

    Precedence: ``SUTRA_MODE`` (validated), then the persisted mode file, then
    the legacy ``SUTRA_SEARCH`` mapping, then ``balanced``. ``source`` is one of
    ``explicit``, ``file``, ``legacy`` or ``default``; only an explicit source
    may disable behaviour that used to be on (notably exact-duplicate reuse).
    Legacy variables keep working unchanged, and an installation that wants the
    old untouched output selects ``off`` once.
    """
    raw = os.environ.get("SUTRA_MODE")
    if raw is not None and str(raw).strip():
        value = str(raw).strip().lower()
        value = _MODE_ALIASES.get(value, value)
        if value not in MODES:
            _warn_once(f"SUTRA_MODE={raw!r} is not one of "
                       f"{', '.join(MODES)}; ignoring it and resolving the "
                       "next configured source")
        else:
            return value, "explicit"
    file_value = _read_mode_file()
    if file_value:
        if file_value in MODES:
            return file_value, "file"
        _warn_once(f"{mode_file_path()} names an unknown mode "
                   f"{file_value!r}; ignoring it and resolving the next "
                   "configured source")
    legacy = _search_mode()
    if os.environ.get("SUTRA_SEARCH", "").strip().lower() == "off":
        # An explicit legacy opt-out is a choice, not an absent setting; do not
        # upgrade it to the new ``balanced`` default behind the user's back.
        return "off", "legacy"
    if legacy == "safe":
        return "balanced", "legacy"
    if legacy == "balanced":
        return "balanced", "legacy"
    if legacy == "aggressive":
        return "aggressive", "legacy"
    return DEFAULT_MODE, "default"


def write_mode(mode: str, path: str | None = None) -> str:
    """Persist ``mode`` for future processes and return the file path.

    Raises ``ValueError`` for an unknown mode rather than writing a file the
    resolver would ignore.
    """
    value = str(mode or "").strip().lower()
    value = _MODE_ALIASES.get(value, value)
    if value not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose {', '.join(MODES)}")
    target = os.path.abspath(os.path.expanduser(path or mode_file_path()))
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = f"{target}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target


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
    search_mode: str = DEFAULT_SEARCH_MODE
    search_passes: int = DEFAULT_SEARCH_PASSES
    search_budget_ms: int = DEFAULT_SEARCH_BUDGET_MS
    language: bool = DEFAULT_LANGUAGE
    ledger_path: str = ""
    replay_enabled: bool = True
    replay_path: str = ""
    replay_ttl_seconds: int = DEFAULT_REPLAY_TTL_SECONDS
    replay_max_entries: int = DEFAULT_REPLAY_MAX_ENTRIES
    replay_max_result_bytes: int = DEFAULT_REPLAY_MAX_RESULT_BYTES
    search_overhead: str = DEFAULT_SEARCH_OVERHEAD
    prompt_enabled: bool = DEFAULT_PROMPT
    prompt_keep_last: int = DEFAULT_PROMPT_KEEP_LAST
    prompt_min_message_bytes: int = DEFAULT_PROMPT_MIN_MESSAGE_BYTES
    prompt_min_request_bytes: int = DEFAULT_PROMPT_MIN_REQUEST_BYTES
    prompt_max_messages: int = DEFAULT_PROMPT_MAX_MESSAGES
    prompt_store_host: bool = DEFAULT_PROMPT_STORE_HOST
    prompt_preview: bool = DEFAULT_PROMPT_PREVIEW
    prompt_preview_head: int = DEFAULT_PROMPT_PREVIEW_HEAD
    prompt_preview_tail: int = DEFAULT_PROMPT_PREVIEW_TAIL
    mode: str = DEFAULT_MODE
    mode_source: str = "default"

    @property
    def mode_explicit(self) -> bool:
        """True when ``SUTRA_MODE`` or the persisted mode file chose the mode.

        Legacy ``SUTRA_SEARCH`` values also set ``mode`` (so reporting is
        coherent) but keep their historical policy; only an explicit mode may
        switch an engine that used to be on off, so an unchanged installation
        keeps its output.
        """
        return self.mode_source in ("explicit", "file")

    @property
    def reuse_active(self) -> bool:
        """Exact-duplicate reuse, disabled only by an explicit ``off`` mode."""
        if self.mode_explicit and self.mode == "off":
            return False
        return self.reuse

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve() -> Config:
    """Parse the environment once and return the whole configuration."""
    cache = cache_dir()
    enabled = _flag("SUTRA_ENABLED", True)
    mode, mode_source = _resolve_mode()
    if not enabled:
        # A disabled plugin must not advertise a mode that cannot act.
        mode, mode_source = "off", mode_source
    # ``off`` never runs the request stage, so it gets the balanced caps: the
    # values only matter when something could act on them.
    caps = PROMPT_CAPS_BY_MODE.get(mode, PROMPT_CAPS_BY_MODE["balanced"])
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
        search_mode=_search_mode(),
        search_passes=_int("SUTRA_SEARCH_PASSES", DEFAULT_SEARCH_PASSES,
                           minimum=1, maximum=16),
        search_budget_ms=_int("SUTRA_SEARCH_BUDGET_MS",
                              DEFAULT_SEARCH_BUDGET_MS, minimum=1,
                              maximum=5000),
        language=_flag("SUTRA_LANGUAGE", DEFAULT_LANGUAGE),
        mode=mode,
        mode_source=mode_source,
        ledger_path=os.path.abspath(os.path.expanduser(
            os.environ.get("SUTRA_LEDGER_FILE") or "")) if
        os.environ.get("SUTRA_LEDGER_FILE") else "",
        replay_enabled=_flag("SUTRA_REPLAY", True),
        replay_path=os.path.abspath(os.path.expanduser(
            os.environ.get("SUTRA_REPLAY_FILE")
            or os.path.join(cache, "sutra-replay.json"))),
        replay_ttl_seconds=_int("SUTRA_REPLAY_TTL_SECONDS",
                                DEFAULT_REPLAY_TTL_SECONDS),
        replay_max_entries=_int("SUTRA_REPLAY_MAX_ENTRIES",
                                DEFAULT_REPLAY_MAX_ENTRIES, minimum=1),
        search_overhead=_search_overhead(),
        prompt_enabled=_flag("SUTRA_PROMPT", DEFAULT_PROMPT),
        prompt_keep_last=_int("SUTRA_PROMPT_KEEP_LAST",
                              DEFAULT_PROMPT_KEEP_LAST, minimum=0),
        prompt_min_message_bytes=_int("SUTRA_PROMPT_MIN_MESSAGE_BYTES",
                                      caps["min_message_bytes"],
                                      minimum=0),
        prompt_min_request_bytes=_int("SUTRA_PROMPT_MIN_REQUEST_BYTES",
                                      caps["min_request_bytes"],
                                      minimum=0),
        prompt_store_host=_flag("SUTRA_PROMPT_STORE_HOST", DEFAULT_PROMPT_STORE_HOST),
        prompt_max_messages=_int("SUTRA_PROMPT_MAX_MESSAGES",
                                 caps["max_messages"], minimum=1),
        prompt_preview=_flag("SUTRA_PROMPT_PREVIEW", caps["preview"]),
        prompt_preview_head=_int("SUTRA_PROMPT_PREVIEW_HEAD",
                                  caps["preview_head"], minimum=0),
        prompt_preview_tail=_int("SUTRA_PROMPT_PREVIEW_TAIL",
                                  caps["preview_tail"], minimum=0),
        replay_max_result_bytes=_int("SUTRA_REPLAY_MAX_RESULT_BYTES",
                                     DEFAULT_REPLAY_MAX_RESULT_BYTES,
                                     minimum=1024),
        telemetry_sample_rate=_float("SUTRA_TELEMETRY_SAMPLE_RATE",
                                     DEFAULT_TELEMETRY_SAMPLE_RATE,
                                     minimum=0.0, maximum=1.0),
    )
