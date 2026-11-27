"""Context intelligence: score, deduplicate and select context under a budget.

SUTRA normally sees one tool result at a time, so this module is the seam for
hosts that *do* hold a set of context items (a conversation window, a set of
documents, several tool outputs) and want a defensible choice about what to
keep. The selection model is:

* value scoring ``V = w_r R + w_i I + w_d D + w_u U + w_t T`` over normalized
  inputs (``Item``, ``Weights``, ``_values``);
* exact 0/1 knapsack selection when no dependencies cross the candidates, and
  dependency-closure greedy selection otherwise (``_select``);
* mandatory items are always kept, and an impossible mandatory budget is
  reported as a conflict instead of silently dropping them;
* dependency sets are validated: cycles raise ``CycleError`` and a missing
  dependency makes an item unselectable (with a reason), never silently kept;
* near-duplicate detection with a 64-bit SimHash (``simhash``) and a MinHash
  signature provided for comparison (``minhash``); duplicates are only
  *reported and optionally dropped* when nothing depends on them;
* incremental hashing: ``Context.update`` only re-fingerprints content that
  actually changed.

Everything is deterministic: no timestamps are read, no randomness is used, and
every tie is broken by item id. Costs are integers. A token cost that the caller
did not supply is estimated as ``len(utf8) // 4`` and the selection is marked
``estimated`` so no caller can mistake it for a measurement.

Recovery is the caller's job: selection returns ids and reasons, it never
destroys or rewrites an item, so a dropped item is still the host's to keep.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

SIMHASH_BITS = 64
DEFAULT_NEAR_DISTANCE = 16
DEFAULT_BUDGET_UNIT = "tokens"
MAX_EXACT_ITEMS = 200
MAX_EXACT_UNITS = 4096
TOKEN_BYTES = 4  # the documented byte-based estimate, never called a measurement


class CycleError(ValueError):
    """A dependency graph contains a cycle; the message names one."""

    def __init__(self, path: Sequence[str]) -> None:
        self.path = tuple(path)
        super().__init__("dependency cycle: " + " -> ".join(self.path))


@dataclass(frozen=True)
class Weights:
    """Weights for ``V = w_r R + w_i I + w_d D + w_u U + w_t T``."""

    relevance: float = 0.35
    importance: float = 0.30
    dependency: float = 0.15
    usefulness: float = 0.10
    recency: float = 0.10

    def __post_init__(self) -> None:
        total = 0.0
        for name in ("relevance", "importance", "dependency", "usefulness",
                     "recency"):
            value = getattr(self, name)
            if not _finite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")
            total += value
        if total <= 0:
            raise ValueError("at least one weight must be greater than zero")

    def as_dict(self) -> dict[str, float]:
        return {
            "relevance": self.relevance,
            "importance": self.importance,
            "dependency": self.dependency,
            "usefulness": self.usefulness,
            "recency": self.recency,
        }


def _finite(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _ratio(value: object, name: str) -> float:
    if not _finite(value) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return float(value)


def fingerprint(content: str) -> str:
    """SHA-256 of the content, over its UTF-8 bytes with surrogatepass."""
    return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Item:
    """One context item, with the documented fields and no hidden state."""

    id: str
    content: str
    token_cost: int | None = None
    relevance: float = 0.0
    importance: float = 0.0
    usefulness: float = 0.0
    recency: float | None = None  # explicit [0, 1]; wins over last_updated
    dependency: tuple[str, ...] = ()
    source: str = ""
    last_updated: float | None = None  # timestamp in seconds, caller's clock
    hash: str = ""
    confidence: float = 1.0
    mandatory: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("id must be a non-empty string")
        if not isinstance(self.content, str):
            raise ValueError("content must be a string")
        if self.token_cost is not None and (
                not isinstance(self.token_cost, int)
                or isinstance(self.token_cost, bool)
                or self.token_cost < 0):
            raise ValueError("token_cost must be a non-negative int or None")
        for name in ("relevance", "importance", "usefulness"):
            object.__setattr__(self, name, _ratio(getattr(self, name), name))
        object.__setattr__(self, "confidence", _ratio(self.confidence, "confidence"))
        if self.recency is not None:
            object.__setattr__(self, "recency", _ratio(self.recency, "recency"))
        if self.last_updated is not None and not _finite(self.last_updated):
            raise ValueError("last_updated must be a finite timestamp or None")
        deps = tuple(self.dependency)
        for dep in deps:
            if not isinstance(dep, str) or not dep.strip():
                raise ValueError("dependency entries must be non-empty ids")
            if dep == self.id:
                raise ValueError(f"item {self.id!r} cannot depend on itself")
        object.__setattr__(self, "dependency", tuple(dict.fromkeys(deps)))
        if not isinstance(self.hash, str):
            raise ValueError("hash must be a string")
        if not isinstance(self.mandatory, bool):
            raise ValueError("mandatory must be a bool")
        if not self.hash:
            object.__setattr__(self, "hash", fingerprint(self.content))

    def cost(self, unit: str = DEFAULT_BUDGET_UNIT) -> int:
        """Cost in ``unit``; a missing token_cost is an estimate."""
        if unit == "bytes":
            return len(self.content.encode("utf-8", "surrogatepass"))
        if unit != "tokens":
            raise ValueError("unit must be 'tokens' or 'bytes'")
        if self.token_cost is not None:
            return self.token_cost
        return len(self.content.encode("utf-8", "surrogatepass")) // TOKEN_BYTES

    def estimated(self, unit: str = DEFAULT_BUDGET_UNIT) -> bool:
        return unit == "tokens" and self.token_cost is None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "token_cost": self.token_cost,
            "relevance": self.relevance,
            "importance": self.importance,
            "usefulness": self.usefulness,
            "recency": self.recency,
            "dependency": list(self.dependency),
            "source": self.source,
            "last_updated": self.last_updated,
            "hash": self.hash,
            "confidence": self.confidence,
            "mandatory": self.mandatory,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Item":
        """Build an item from a mapping; unknown keys are ignored on purpose."""
        if not isinstance(data, Mapping):
            raise ValueError("an item must be a mapping")
        deps = data.get("dependency", data.get("dependencies", ()))
        if isinstance(deps, str):
            deps = (deps,)
        elif deps is None:
            deps = ()
        else:
            deps = tuple(deps)  # type: ignore[arg-type]
        return cls(
            id=data.get("id"),  # type: ignore[arg-type]
            content=data.get("content", ""),  # type: ignore[arg-type]
            token_cost=data.get("token_cost"),  # type: ignore[arg-type]
            relevance=float(data.get("relevance", 0.0)),  # type: ignore[arg-type]
            importance=float(data.get("importance", 0.0)),  # type: ignore[arg-type]
            usefulness=float(data.get("usefulness", 0.0)),  # type: ignore[arg-type]
            recency=(None if data.get("recency") is None
                     else float(data["recency"])),  # type: ignore[arg-type]
            dependency=deps,  # type: ignore[arg-type]
            source=str(data.get("source", "")),
            last_updated=(None if data.get("last_updated") is None
                          else float(data["last_updated"])),  # type: ignore[arg-type]
            hash=str(data.get("hash", "")),
            confidence=float(data.get("confidence", 1.0)),  # type: ignore[arg-type]
            mandatory=bool(data.get("mandatory", False)),
        )


@dataclass(frozen=True)
class Selection:
    """What a context budget can keep, and exactly why everything else went."""

    selected: tuple[str, ...]
    dropped: tuple[tuple[str, str], ...]  # (id, reason), sorted by id
    total_cost: int
    budget: int
    value: float
    unit: str
    feasible: bool = True
    conflict: str | None = None
    duplicates: tuple[tuple[str, str], ...] = ()  # (dropped_id, kept_id)
    estimated: bool = False
    algorithm: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "selected": list(self.selected),
            "dropped": [{"id": item_id, "reason": reason}
                        for item_id, reason in self.dropped],
            "total_cost": self.total_cost,
            "budget": self.budget,
            "value": self.value,
            "unit": self.unit,
            "feasible": self.feasible,
            "conflict": self.conflict,
            "duplicates": [{"dropped": dropped, "kept": kept}
                           for dropped, kept in self.duplicates],
            "estimated": self.estimated,
            "algorithm": self.algorithm,
        }


class Context:
    """A keyed set of items with cached fingerprints and one selection API."""

    def __init__(self, items: Iterable[Item] = ()) -> None:
        self._items: dict[str, Item] = {}
        for item in items:
            self.add(item)

    def add(self, item: Item) -> Item:
        if not isinstance(item, Item):
            raise ValueError("add() takes an Item")
        self._items[item.id] = item
        return item

    def update(self, item_id: str, *, content: str | None = None,
               **changes: object) -> Item:
        """Replace fields; the fingerprint is recomputed only for new content."""
        current = self._items[item_id]
        if content is not None and content != current.content:
            changes["content"] = content
            changes["hash"] = ""
        elif content is not None:
            changes.pop("hash", None)
        updated = replace(current, **changes)  # type: ignore[arg-type]
        self._items[item_id] = updated
        return updated

    def remove(self, item_id: str) -> Item:
        return self._items.pop(item_id)

    def get(self, item_id: str) -> Item:
        return self._items[item_id]

    def items(self) -> tuple[Item, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, item_id: object) -> bool:
        return item_id in self._items

    def select(self, budget: int, *, unit: str = DEFAULT_BUDGET_UNIT,
               weights: Weights | None = None,
               near_duplicates: bool = False,
               near_distance: int = DEFAULT_NEAR_DISTANCE) -> Selection:
        """Select items under ``budget``; see the module docstring."""
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise ValueError("budget must be a non-negative int")
        if unit not in ("tokens", "bytes"):
            raise ValueError("unit must be 'tokens' or 'bytes'")
        if not isinstance(near_distance, int) or not 0 <= near_distance <= SIMHASH_BITS:
            raise ValueError("near_distance must be an int in [0, 64]")
        return select(self.items(), budget, unit=unit,
                      weights=weights, near_duplicates=near_duplicates,
                      near_distance=near_distance)


# --------------------------------------------------------------------------
# Scoring and graph helpers


def _dependents(items: Sequence[Item]) -> dict[str, set[str]]:
    """Reverse edges: item id -> ids that directly require it."""
    result: dict[str, set[str]] = {item.id: set() for item in items}
    for item in items:
        for dep in item.dependency:
            result.setdefault(dep, set()).add(item.id)
    return result


def dependency_values(items: Sequence[Item]) -> dict[str, float]:
    """``D_i``: share of items that transitively depend on ``i``, in [0, 1].

    One reverse breadth-first walk per item counts every ancestor that must be
    present with it. This is O(V * (V + E)) worst case, which is fine for the
    context sizes a single request produces; the previous pair-by-pair form was
    O(V^2) walks and dominated the 1000-item benchmark.
    """
    reverse = _dependents(items)
    counts = {item.id: 0 for item in items}
    for target in counts:
        seen: set[str] = set()
        stack = [target]
        while stack:
            node = stack.pop()
            for dependent in reverse.get(node, ()):  # items needing node
                if dependent in seen:
                    continue
                seen.add(dependent)
                stack.append(dependent)
        counts[target] = len(seen)
    peak = max(counts.values(), default=0)
    return {
        key: (count / peak if peak else 0.0) for key, count in counts.items()
    }


def detect_cycle(items: Sequence[Item]) -> tuple[str, ...] | None:
    """Return one dependency cycle as a path, or ``None``."""
    edges = {item.id: item.dependency for item in items}
    state: dict[str, int] = {}  # 0 unseen, 1 in stack, 2 done
    stack: list[str] = []

    def visit(node: str) -> tuple[str, ...] | None:
        state[node] = 1
        stack.append(node)
        for dep in edges.get(node, ()):
            if dep not in edges:
                continue
            if state.get(dep) == 1:
                return tuple(stack[stack.index(dep):] + [dep])
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found is not None:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in edges:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found is not None:
                return found
    return None


def values(items: Sequence[Item], weights: Weights | None = None,
           ) -> dict[str, float]:
    """Score every item, normalizing recency across the batch."""
    chosen = weights or Weights()
    stamps = [item.last_updated for item in items if item.last_updated is not None]
    low = min(stamps) if stamps else None
    high = max(stamps) if stamps else None
    dep = dependency_values(items)
    result: dict[str, float] = {}
    for item in items:
        if item.recency is not None:
            recency = item.recency
        elif item.last_updated is not None and low is not None and high is not None:
            recency = 1.0 if high == low else (
                (item.last_updated - low) / (high - low))
        else:
            recency = 0.0
        result[item.id] = (
            chosen.relevance * item.relevance
            + chosen.importance * item.importance
            + chosen.dependency * dep[item.id]
            + chosen.usefulness * item.usefulness
            + chosen.recency * recency
        )
    return result


# --------------------------------------------------------------------------
# Near-duplicate signatures


def _shingles(text: str, size: int = 3) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    if len(words) <= size:
        return [" ".join(words)]
    return [" ".join(words[index:index + size])
            for index in range(len(words) - size + 1)]


def _hash_token(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8", "surrogatepass"),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash(text: str) -> int:
    """64-bit SimHash over whitespace-split 3-shingles; 0 for empty text."""
    vector = [0] * SIMHASH_BITS
    shingles = _shingles(text)
    for shingle in shingles:
        hashed = _hash_token(shingle)
        for bit in range(SIMHASH_BITS):
            vector[bit] += 1 if hashed & (1 << bit) else -1
    signature = 0
    for bit, weight in enumerate(vector):
        if weight > 0:
            signature |= 1 << bit
    return signature


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def minhash(text: str, size: int = 32) -> tuple[int, ...]:
    """MinHash signature for comparison with SimHash; same shingles."""
    if size <= 0:
        raise ValueError("size must be positive")
    seeds = [1, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53,
             59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113,
             127, 131]
    if size > len(seeds):
        raise ValueError(f"size must be at most {len(seeds)}")
    tokens = sorted({_hash_token(shingle) for shingle in _shingles(text)})
    return tuple(min((token ^ seed) * 0x9E3779B97F4A7C15 & 0xFFFFFFFFFFFFFFFF
                     for token in tokens) for seed in seeds[:size])


def jaccard(left: Sequence[int], right: Sequence[int]) -> float:
    """Estimated Jaccard similarity for two equal-length MinHash signatures."""
    if not left or len(left) != len(right):
        return 0.0
    return sum(1 for a, b in zip(left, right) if a == b) / len(left)


# A near-duplicate is only dropped when the representative carries at least this
# share of the duplicate's vocabulary, so a "duplicate" that happens to carry a
# distinct error or identifier is kept instead of silently elided.
NEAR_DUPLICATE_CONTAINMENT = 0.9
_VOCABULARY_LIMIT = 512


def _vocabulary(text: str) -> set[str]:
    """Lower-cased word tokens, capped so one huge item cannot dominate."""
    words: set[str] = set()
    for word in text.split():
        cleaned = word.strip(".,;:!?()[]{}\"'`").lower()
        if cleaned:
            words.add(cleaned)
        if len(words) >= _VOCABULARY_LIMIT:
            break
    return words


def _containment(duplicate: str, representative: str) -> float:
    """Share of ``duplicate``'s vocabulary that ``representative`` carries."""
    duplicate_words = _vocabulary(duplicate)
    if not duplicate_words:
        return 1.0
    return len(duplicate_words & _vocabulary(representative)) / len(duplicate_words)


def _duplicate_groups(items: Sequence[Item], distance: int,
                      ) -> list[tuple[str, ...]]:
    """Group ids whose SimHash is within ``distance`` of the group's first id."""
    groups: list[tuple[int, list[str]]] = []
    for item in sorted(items, key=lambda value: value.id):
        signature = simhash(item.content)
        for reference, ids in groups:
            if hamming(reference, signature) <= distance:
                ids.append(item.id)
                break
        else:
            groups.append((signature, [item.id]))
    return [tuple(ids) for signature, ids in groups if len(ids) > 1]


# --------------------------------------------------------------------------
# Selection


def select(items: Iterable[Item], budget: int, *,
           unit: str = DEFAULT_BUDGET_UNIT,
           weights: Weights | None = None,
           near_duplicates: bool = False,
           near_distance: int = DEFAULT_NEAR_DISTANCE) -> Selection:
    """Choose the highest-value set of items that fits ``budget``.

    Mandatory items and everything they depend on are kept first. An
    impossible mandatory budget is reported (``feasible=False``) and still
    returned rather than dropped. If dependencies cross the candidates, a
    dependency-closure greedy pass is used; when all remaining dependencies are
    already satisfied and the instance is small enough, an exact 0/1 knapsack
    replaces it. Near-duplicates are only dropped when nothing depends on them,
    and a kept duplicate is always preferred by value.
    """
    batch = tuple(items)
    seen: dict[str, Item] = {}
    for item in batch:
        if item.id in seen:
            raise ValueError(f"duplicate item id {item.id!r}")
        seen[item.id] = item
    batch = tuple(seen[key] for key in sorted(seen))
    if budget < 0:
        raise ValueError("budget must be non-negative")
    cycle = detect_cycle(batch)
    if cycle is not None:
        raise CycleError(cycle)
    if not batch:
        return Selection((), (), 0, budget, 0.0, unit,
                         algorithm="empty")

    weights = weights or Weights()
    score = values(batch, weights)
    costs = {item.id: item.cost(unit) for item in batch}
    estimated = any(item.estimated(unit) for item in batch)
    by_id = {item.id: item for item in batch}
    dependents = _dependents(batch)
    dropped: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []

    mandatory_ids = [item.id for item in batch if item.mandatory]
    blockers: set[str] = set()
    for item in batch:
        if any(dep not in by_id for dep in item.dependency):
            blockers.add(item.id)

    def closure(start: str) -> tuple[str, ...] | None:
        """Ids that must be selected with ``start``; None if one is missing."""
        needed: list[str] = []
        stack = [start]
        seen_deps: set[str] = set()
        while stack:
            current = stack.pop()
            for dep in by_id[current].dependency:
                if dep not in by_id:
                    return None
                if dep not in seen_deps:
                    seen_deps.add(dep)
                    needed.append(dep)
                    stack.append(dep)
        return tuple(needed)

    selected: dict[str, None] = {}
    mandatory = set(mandatory_ids)
    for item_id in sorted(mandatory):
        needed = closure(item_id)
        if needed is None:
            return _conflict(batch, budget, unit, score, costs, dropped,
                             f"missing dependency for mandatory item {item_id!r}")
        for dep in needed:
            mandatory.add(dep)
    mandatory_cost = sum(costs[item_id] for item_id in mandatory)
    if mandatory_cost > budget:
        for item_id in mandatory:
            selected[item_id] = None
        for item in batch:
            if item.id not in mandatory:
                dropped[item.id] = "budget exceeded by mandatory context"
        return Selection(
            tuple(sorted(selected)), tuple(sorted(dropped.items())),
            mandatory_cost, budget, _total(score, selected), unit,
            feasible=False,
            conflict=(f"mandatory context needs {mandatory_cost} {unit} "
                      f"but the budget is {budget}"),
            duplicates=tuple(duplicates), estimated=estimated,
            algorithm="mandatory-conflict")

    for item_id in sorted(mandatory):
        selected[item_id] = None

    # Near-duplicates: report always, drop only when safe.
    if near_duplicates:
        for group in _duplicate_groups(batch, near_distance):
            group_items = [by_id[item_id] for item_id in group]
            keep_mandatory = [item for item in group_items if item.mandatory]
            if keep_mandatory:
                representative = max(keep_mandatory, key=lambda item: item.id)
            else:
                representative = max(
                    group_items,
                    key=lambda item: (score[item.id], -costs[item.id], item.id))
            for item in sorted(group_items, key=lambda value: value.id):
                if item.id == representative.id or item.mandatory:
                    continue
                if dependents.get(item.id):
                    continue  # something needs it; keep the copy
                if _containment(item.content, representative.content) < NEAR_DUPLICATE_CONTAINMENT:
                    continue  # carries vocabulary the representative lacks
                duplicates.append((item.id, representative.id))
                dropped[item.id] = (
                    f"near-duplicate of {representative.id!r} "
                    f"(distance <= {near_distance})")

    candidates = [
        item for item in batch
        if item.id not in selected and item.id not in dropped
    ]
    for item in candidates:
        if item.id in blockers:
            needed = closure(item.id)
            if needed is None:
                dropped[item.id] = "missing dependency"
    candidates = [item for item in candidates if item.id not in dropped]

    remaining = budget - mandatory_cost
    exact = all(
        all(dep in selected or dep == item.id for dep in item.dependency)
        for item in candidates)
    if exact and len(candidates) <= MAX_EXACT_ITEMS and remaining <= MAX_EXACT_UNITS:
        chosen = _exact_knapsack(candidates, costs, score, remaining)
        algorithm = "exact-knapsack"
    else:
        chosen = _greedy(candidates, by_id, costs, score, selected,
                         remaining, dropped, unit)
        algorithm = ("greedy-optimal" if exact else "greedy-closure")
    for item_id in chosen:
        selected[item_id] = None
    for item in batch:
        if item.id not in selected and item.id not in dropped:
            dropped[item.id] = "not selected: budget or lower value"
    return Selection(
        tuple(sorted(selected)), tuple(sorted(dropped.items())),
        sum(costs[item_id] for item_id in selected), budget,
        _total(score, selected), unit, duplicates=tuple(duplicates),
        estimated=estimated, algorithm=algorithm,
    )


def _total(score: Mapping[str, float], selected: Mapping[str, None]) -> float:
    return round(sum(score[item_id] for item_id in selected), 12)


def _conflict(batch: Sequence[Item], budget: int, unit: str,
              score: Mapping[str, float], costs: Mapping[str, int],
              dropped: dict[str, str], reason: str) -> Selection:
    return Selection(
        (), tuple(sorted([(item.id, reason) for item in batch]
                         + list(dropped.items()))), 0, budget, 0.0,
        unit, feasible=False, conflict=reason, algorithm="invalid")


def _exact_knapsack(candidates: Sequence[Item], costs: Mapping[str, int],
                    score: Mapping[str, float], budget: int) -> tuple[str, ...]:
    """0/1 knapsack by value, O(n * budget) time and one row of values.

    Only one row of best values is needed: the recurrence reads capacities
    below the one it writes, so walking downward in place never reads a value
    this item produced. One byte per (item, capacity) records the takes the
    reconstruction needs, which is what keeps the answer identical to the
    matrix form without keeping the matrix.
    """
    ordered = sorted(candidates, key=lambda item: item.id)
    best = [0.0] * (budget + 1)
    taken: list[bytearray] = []
    for item in ordered:
        cost = costs[item.id]
        value = score[item.id]
        row = bytearray(budget + 1)
        if cost <= budget:
            for capacity in range(budget, cost - 1, -1):
                candidate = best[capacity - cost] + value
                if candidate >= best[capacity] - 1e-15:
                    best[capacity] = candidate
                    row[capacity] = 1
        taken.append(row)
    chosen: list[str] = []
    capacity = budget
    for index in range(len(ordered) - 1, -1, -1):
        if taken[index][capacity]:
            chosen.append(ordered[index].id)
            capacity -= costs[ordered[index].id]
    return tuple(reversed(chosen))


def _greedy(candidates: Sequence[Item], by_id: Mapping[str, Item],
            costs: Mapping[str, int], score: Mapping[str, float],
            already: Mapping[str, None], budget: int,
            dropped: dict[str, str], unit: str) -> tuple[str, ...]:
    """Dependency-closure greedy, run by density and by value; keep the best."""
    base = set(already)

    def closure(start: str, picked: Mapping[str, None],
                ) -> tuple[str, ...] | None:
        needed: list[str] = []
        stack = [start]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            for dep in by_id[node].dependency:
                if dep not in by_id:
                    return None
                if dep in base or dep in picked or dep in seen:
                    continue
                seen.add(dep)
                needed.append(dep)
                stack.append(dep)
        return tuple(needed)

    def run(key) -> tuple[tuple[str, ...], dict[str, str]]:
        remaining = budget
        picked: dict[str, None] = {}
        reasons: dict[str, str] = {}
        for item in sorted(candidates, key=key):
            if item.id in picked:
                continue
            needed = closure(item.id, picked)
            if needed is None:
                reasons[item.id] = "missing dependency"
                continue
            total = sum(costs[dep] for dep in needed) + costs[item.id]
            if total <= remaining:
                for dep in needed:
                    picked[dep] = None
                picked[item.id] = None
                remaining -= total
            else:
                reasons[item.id] = (
                    f"not selected: needs {total} {unit} but only "
                    f"{remaining} remained")
        return tuple(picked), reasons

    by_density = run(lambda item: (
        -(score[item.id] / costs[item.id] if costs[item.id] else float("inf")),
        -score[item.id], item.id))
    by_value = run(lambda item: (-score[item.id], costs[item.id], item.id))
    chosen, reasons = max(
        (by_density, by_value),
        key=lambda result: (sum(score[item_id] for item_id in result[0]),
                            len(result[0])))
    for item_id, reason in reasons.items():
        dropped.setdefault(item_id, reason)
    return chosen


__all__ = [
    "Context", "CycleError", "DEFAULT_NEAR_DISTANCE", "Item", "Selection",
    "Weights", "detect_cycle", "dependency_values", "fingerprint", "hamming",
    "jaccard", "minhash", "select", "simhash", "values",
]
