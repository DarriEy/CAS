# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""In-memory LRU result cache with TTL for extracted attribute results."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from cas.core.models import AggregationMethod, AttributeResult, TimeRange

DEFAULT_RESULT_TTL = 600
DEFAULT_MAX_ENTRIES = 10_000


@dataclass
class ResultEntry:
    result: AttributeResult
    cached_at: float = field(default_factory=time.monotonic)
    ttl: float = DEFAULT_RESULT_TTL

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.cached_at) > self.ttl


class ResultCache:
    """In-memory LRU cache for per-dataset extraction results."""

    def __init__(
        self,
        default_ttl: float = DEFAULT_RESULT_TTL,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._cache: OrderedDict[str, ResultEntry] = OrderedDict()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(
        dataset_id: str,
        geometry_hash: str,
        aggregation: AggregationMethod,
        time_range: TimeRange | None,
    ) -> str:
        raw = json.dumps({
            "dataset_id": dataset_id,
            "geometry_hash": geometry_hash,
            "aggregation": aggregation.value,
            "time_range": (
                [time_range.start.isoformat(), time_range.end.isoformat()]
                if time_range
                else None
            ),
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, key: str) -> AttributeResult | None:
        entry = self._cache.get(key)
        if entry is None or entry.is_expired:
            if entry is not None:
                del self._cache[key]
            self._misses += 1
            return None
        self._cache.move_to_end(key)
        self._hits += 1
        return entry.result

    def set(self, key: str, result: AttributeResult) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = ResultEntry(result=result, ttl=self._default_ttl)
        else:
            self._cache[key] = ResultEntry(result=result, ttl=self._default_ttl)
            if len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
        }
