"""Read provider usage metadata into :class:`cost.Usage`.

goals.md PART 13 asks for provider-independent accounting that understands the
usage metadata each provider actually returns, including cached input and
cache-write tokens, and PART 14 forbids double counting. This module is the
adapter between a raw response body and the explicit accounting model:

``openai``     ``usage.prompt_tokens`` / ``completion_tokens`` plus
               ``prompt_tokens_details.cached_tokens``
``anthropic``  ``usage.input_tokens``, ``output_tokens``,
               ``cache_read_input_tokens`` and ``cache_creation_input_tokens``
``google``     ``usageMetadata.promptTokenCount`` / ``candidatesTokenCount`` plus
               ``cachedContentTokenCount``
``deepseek``   ``usage.prompt_tokens`` / ``completion_tokens`` plus
               ``prompt_cache_hit_tokens`` (a subset of prompt tokens)

Nothing here guesses a field: a missing required count raises ``ValueError``
with the provider name, and a response that does not look like the named
provider's shape is rejected. No provider call is made and no price is assumed;
feed the result to :func:`cost.price` with caller-supplied rates.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import cost

PROVIDERS = ("openai", "anthropic", "google", "deepseek")


def _count(value: Any, name: str, provider: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"{provider}: {name} must be a non-negative integer, got {value!r}")
    return value


def _mapping(value: Any, name: str, provider: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{provider}: {name} must be an object")
    return value


def _get(data: Mapping[str, Any], *paths: str) -> Any:
    """First present value along the dotted paths, else ``None``."""
    for path in paths:
        node: Any = data
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            return node
    return None


def _openai(payload: Mapping[str, Any]) -> cost.Usage:
    usage = _mapping(payload.get("usage"), "usage", "openai")
    prompt = _get(usage, "prompt_tokens", "input_tokens")
    completion = _get(usage, "completion_tokens", "output_tokens")
    if prompt is None or completion is None:
        raise ValueError("openai: usage lacks prompt/input and completion/output")
    cached = _get(usage, "prompt_tokens_details.cached_tokens",
                  "input_tokens_details.cached_tokens") or 0
    return cost.Usage(
        input_tokens=_count(prompt, "prompt_tokens", "openai"),
        output_tokens=_count(completion, "completion_tokens", "openai"),
        cached_input_tokens=_count(cached, "cached_tokens", "openai"),
    )


def _anthropic(payload: Mapping[str, Any]) -> cost.Usage:
    usage = _mapping(payload.get("usage"), "usage", "anthropic")
    base = _get(usage, "input_tokens")
    output = _get(usage, "output_tokens")
    if base is None or output is None:
        raise ValueError("anthropic: usage lacks input_tokens or output_tokens")
    read = _get(usage, "cache_read_input_tokens") or 0
    write = _get(usage, "cache_creation_input_tokens") or 0
    base = _count(base, "input_tokens", "anthropic")
    read = _count(read, "cache_read_input_tokens", "anthropic")
    write = _count(write, "cache_creation_input_tokens", "anthropic")
    # Anthropic reports cache tokens outside input_tokens; fold them in so the
    # input total means "everything processed", with the subsets marked.
    return cost.Usage(
        input_tokens=base + read + write,
        output_tokens=_count(output, "output_tokens", "anthropic"),
        cached_input_tokens=read,
        cache_write_tokens=write,
    )


def _google(payload: Mapping[str, Any]) -> cost.Usage:
    usage = _mapping(payload.get("usageMetadata"), "usageMetadata", "google")
    prompt = _get(usage, "promptTokenCount")
    completion = _get(usage, "candidatesTokenCount")
    if prompt is None or completion is None:
        raise ValueError(
            "google: usageMetadata lacks promptTokenCount or candidatesTokenCount")
    cached = _get(usage, "cachedContentTokenCount") or 0
    return cost.Usage(
        input_tokens=_count(prompt, "promptTokenCount", "google"),
        output_tokens=_count(completion, "candidatesTokenCount", "google"),
        cached_input_tokens=_count(cached, "cachedContentTokenCount", "google"),
    )


def _deepseek(payload: Mapping[str, Any]) -> cost.Usage:
    usage = _mapping(payload.get("usage"), "usage", "deepseek")
    prompt = _get(usage, "prompt_tokens")
    completion = _get(usage, "completion_tokens")
    if prompt is None or completion is None:
        raise ValueError("deepseek: usage lacks prompt_tokens or completion_tokens")
    hit = _get(usage, "prompt_cache_hit_tokens") or 0
    prompt = _count(prompt, "prompt_tokens", "deepseek")
    hit = _count(hit, "prompt_cache_hit_tokens", "deepseek")
    if hit > prompt:
        raise ValueError(
            f"deepseek: prompt_cache_hit_tokens {hit} exceeds prompt_tokens {prompt}")
    return cost.Usage(
        input_tokens=prompt,
        output_tokens=_count(completion, "completion_tokens", "deepseek"),
        cached_input_tokens=hit,
    )


_PARSERS = {
    "openai": _openai,
    "anthropic": _anthropic,
    "google": _google,
    "deepseek": _deepseek,
}


def usage_from(provider: str, payload: Mapping[str, Any]) -> cost.Usage:
    """Return the usage a supported provider reported in ``payload``.

    ``provider`` must be one of :data:`PROVIDERS`; an unsupported name raises
    ``ValueError`` rather than trying to guess a shape. The result never
    invents a count: missing fields are an error, zero is only used where the
    provider documents the field as optional (cache counters).
    """
    if not isinstance(provider, str):
        raise ValueError("provider must be a string")
    key = provider.strip().lower()
    parser = _PARSERS.get(key)
    if parser is None:
        raise ValueError(
            f"unsupported provider {provider!r}; supported: {', '.join(PROVIDERS)}")
    if not isinstance(payload, Mapping):
        raise ValueError(f"{key}: payload must be an object")
    return parser(payload)


__all__ = ["PROVIDERS", "usage_from"]
