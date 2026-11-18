"""Cache metadata, layer status and economics (goals.md PART 10).

Five cache layers are named by the goals document. This module is explicit
about which of them SUTRA can actually own, because conflating them would
inflate every savings claim:

===== ====================== ==================================================
Layer What it is             Status in SUTRA
===== ====================== ==================================================
1     local result cache     Implemented in ``store.py`` + ``reuse.py``
                             (SHA-256 keyed originals, TTL, size caps)
2     context cache          ``context.py`` selects items under a budget, but a
                             result hook cannot see the conversation, so nothing
                             is cached automatically
3     tool-result cache      Same limitation: the hook sees results, not calls.
                             ``redundancy.py`` classifies repeated calls, but a
                             host has to ask
4     provider prompt cache  Not observable from a post-tool hook: the provider
                             may charge less for cached input, and SUTRA cannot
                             see whether it did or set it up
5     semantic cache         Not implemented: near-duplicate detection exists
                             (SimHash/MinHash in ``context.py``) but no semantic
                             cache is wired, and no embedding model is called
===== ====================== ==================================================

The provider prompt cache is *not* the local result cache. Money is only saved
by the first when the provider reports cached input tokens; this project never
claims that.

What this module provides:

- :class:`Metadata` — the fields a cache entry needs to be invalidatable
  (input fingerprint, provider, model, tool and tool version, config version,
  dependencies, creation, expiry, validation state), with round-trip JSON.
- :func:`cache_key` — one digest over all of those, so an entry cannot be
  served to a different provider, model, tool version or config.
- :class:`Economics` — the break-even arithmetic for a cache layer:
  ``C_write + N * C_read < N * C_miss``, i.e. ``N > C_write / (C_miss - C_read)``.
  Costs are whatever the caller measured; nothing is assumed.
- :func:`layers` / :func:`report` — the table above as data, for the CLI.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Mapping

VERSION = 1

VALIDATION_STATES = ("valid", "expired", "invalidated", "unverified")

#: Machine-readable status of each of the five layers in goals.md PART 10.
LAYERS: tuple[dict[str, str], ...] = (
    {
        "layer": "local-result",
        "status": "implemented",
        "stores": "originals keyed by SHA-256 in store.py",
        "keyed_by": "content hash",
        "invalidated_by": "TTL, size caps, content mismatch, delete",
        "note": "the recovery store; measured and tested",
    },
    {
        "layer": "context",
        "status": "library-only",
        "stores": "nothing automatically",
        "keyed_by": "context item ids and hashes",
        "invalidated_by": "dependency snapshot (redundancy.py)",
        "note": "context.py can select items; a result hook cannot observe the "
                "conversation, so nothing is cached without a host that asks",
    },
    {
        "layer": "tool-result",
        "status": "library-only",
        "stores": "nothing automatically",
        "keyed_by": "call fingerprint plus dependency snapshot",
        "invalidated_by": "changed dependency, expiry, mandatory command",
        "note": "redundancy.py classifies; the hook sees results, not calls",
    },
    {
        "layer": "provider-prompt",
        "status": "not-observable",
        "stores": "provider-side prefix cache",
        "keyed_by": "provider-specific prefix",
        "invalidated_by": "provider policy",
        "note": "SUTRA cannot see cached-input tokens or shape the prefix from "
                "a post-tool hook; never counted as a SUTRA saving",
    },
    {
        "layer": "semantic",
        "status": "not-implemented",
        "stores": "nothing",
        "keyed_by": "would need embeddings",
        "invalidated_by": "n/a",
        "note": "near-duplicate detection exists, a semantic cache is not wired, "
                "and no model is called",
    },
)


def layers() -> tuple[dict[str, str], ...]:
    """The five layers with their honest status."""
    return tuple(dict(entry) for entry in LAYERS)


def report() -> dict[str, object]:
    """A JSON-ready summary: layers, implemented count, provider caveat."""
    entries = layers()
    return {
        "layers": entries,
        "implemented": [entry["layer"] for entry in entries
                        if entry["status"] == "implemented"],
        "library_only": [entry["layer"] for entry in entries
                         if entry["status"] == "library-only"],
        "not_implemented": [entry["layer"] for entry in entries
                            if entry["status"] in ("not-observable",
                                                   "not-implemented")],
        "provider_prompt_cache": (
            "Not observable: cached input tokens, if any, are the provider's "
            "doing and are never counted as a SUTRA saving."),
    }


@dataclass(frozen=True)
class Metadata:
    """Everything needed to decide whether a cache entry may still be served."""

    input_fingerprint: str
    provider: str = ""
    model: str = ""
    tool: str = ""
    tool_version: str = ""
    config_version: str = ""
    dependencies: Mapping[str, str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    expires: float | None = None
    validation: str = "valid"

    def __post_init__(self) -> None:
        if not isinstance(self.input_fingerprint, str) or not self.input_fingerprint:
            raise ValueError("input_fingerprint must be a non-empty string")
        for name in ("provider", "model", "tool", "tool_version", "config_version"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
        if self.validation not in VALIDATION_STATES:
            raise ValueError(
                f"validation must be one of {', '.join(VALIDATION_STATES)}")
        if self.expires is not None and not isinstance(self.expires, (int, float)):
            raise TypeError("expires must be a number of seconds or None")
        if not isinstance(self.created, (int, float)):
            raise TypeError("created must be a number of seconds")

    def expired(self, now: float | None = None) -> bool:
        if self.expires is None:
            return False
        return (now if now is not None else time.time()) >= self.expires

    def usable(self, now: float | None = None) -> bool:
        return self.validation == "valid" and not self.expired(now)

    def key(self) -> str:
        """One digest over identity, versions and dependencies."""
        return cache_key(
            self.input_fingerprint, provider=self.provider, model=self.model,
            tool=self.tool, tool_version=self.tool_version,
            config_version=self.config_version, dependencies=self.dependencies)

    def as_dict(self) -> dict[str, object]:
        return {
            "input_fingerprint": self.input_fingerprint,
            "provider": self.provider,
            "model": self.model,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "config_version": self.config_version,
            "dependencies": dict(sorted(self.dependencies.items())),
            "created": self.created,
            "expires": self.expires,
            "validation": self.validation,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Metadata":
        if not isinstance(payload, Mapping):
            raise TypeError("cache metadata must be a mapping")
        dependencies = payload.get("dependencies") or {}
        if not isinstance(dependencies, Mapping):
            raise TypeError("dependencies must be a mapping")
        expires = payload.get("expires")
        created = payload.get("created", time.time())
        return cls(
            input_fingerprint=str(payload.get("input_fingerprint", "")),
            provider=str(payload.get("provider", "")),
            model=str(payload.get("model", "")),
            tool=str(payload.get("tool", "")),
            tool_version=str(payload.get("tool_version", "")),
            config_version=str(payload.get("config_version", "")),
            dependencies={str(k): str(v) for k, v in dependencies.items()},
            created=float(created),
            expires=None if expires is None else float(expires),
            validation=str(payload.get("validation", "valid")),
        )


def cache_key(input_fingerprint: str, *, provider: str = "", model: str = "",
              tool: str = "", tool_version: str = "",
              config_version: str = "",
              dependencies: Mapping[str, str] | None = None) -> str:
    """A key that changes when anything that can invalidate an entry changes.

    The rules from goals.md PART 10 are encoded here: provider, model, tool
    version, config version and dependencies are part of the identity, so a
    cached answer can never leak across a provider or a version change.
    """
    if not isinstance(input_fingerprint, str) or not input_fingerprint:
        raise ValueError("input_fingerprint must be a non-empty string")
    payload = json.dumps({
        "version": VERSION,
        "input": input_fingerprint,
        "provider": provider,
        "model": model,
        "tool": tool,
        "tool_version": tool_version,
        "config_version": config_version,
        "dependencies": dict(sorted((dependencies or {}).items())),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Economics:
    """Break-even arithmetic for one cache layer, in whatever unit you bill in.

    ``write_cost`` is paid once per entry, ``read_cost`` per reuse and
    ``miss_cost`` per call that would have been made without the cache.
    """

    write_cost: float
    read_cost: float
    miss_cost: float

    def __post_init__(self) -> None:
        for name in ("write_cost", "read_cost", "miss_cost"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            if value < 0:
                raise ValueError(f"{name} must not be negative")

    @property
    def saving_per_reuse(self) -> float:
        return self.miss_cost - self.read_cost

    def break_even(self) -> float | None:
        """Reuses needed to pay for one write; ``None`` when reuse cannot win."""
        if self.saving_per_reuse <= 0:
            return None
        if self.write_cost == 0:
            return 0.0
        return self.write_cost / self.saving_per_reuse

    def net(self, reuses: int) -> float:
        """Savings after the write cost for a given number of reuses."""
        if reuses < 0:
            raise ValueError("reuses must not be negative")
        return reuses * self.saving_per_reuse - self.write_cost

    def worth_it(self, reuses: int) -> bool:
        """The goals.md inequality: C_write + N*C_read < N*C_miss."""
        return self.net(reuses) > 0

    def as_dict(self) -> dict[str, object]:
        break_even = self.break_even()
        return {
            "write_cost": self.write_cost,
            "read_cost": self.read_cost,
            "miss_cost": self.miss_cost,
            "saving_per_reuse": self.saving_per_reuse,
            "break_even_reuses": break_even,
            "formula": "C_write + N*C_read < N*C_miss",
        }


def parse_costs(write_cost: float, read_cost: float, miss_cost: float,
                reuses: int) -> dict[str, object]:
    """Convenience wrapper used by the CLI."""
    economics = Economics(write_cost, read_cost, miss_cost)
    payload = economics.as_dict()
    payload["reuses"] = reuses
    payload["net"] = economics.net(reuses)
    payload["worth_it"] = economics.worth_it(reuses)
    return payload


__all__ = ["Economics", "LAYERS", "Metadata", "VALIDATION_STATES",
           "cache_key", "layers", "parse_costs", "report"]
