# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Lightweight in-memory metadata cache with TTL.

CAS does not store data — this caches only dataset catalogs to avoid
re-fetching the catalog on every request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from cas.core.models import Dataset

DEFAULT_TTL_SECONDS = 3600


@dataclass
class CacheEntry:
    datasets: list[Dataset]
    cached_at: float = field(default_factory=time.monotonic)
    ttl: float = DEFAULT_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.cached_at) > self.ttl


class MetadataCache:
    """In-memory TTL cache for provider dataset catalogs."""

    def __init__(self, default_ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl

    def get(self, provider_slug: str) -> list[Dataset] | None:
        entry = self._cache.get(provider_slug)
        if entry is None or entry.is_expired:
            return None
        return entry.datasets

    def set(self, provider_slug: str, datasets: list[Dataset]) -> None:
        self._cache[provider_slug] = CacheEntry(
            datasets=datasets,
            ttl=self._default_ttl,
        )

    def invalidate(self, provider_slug: str) -> None:
        self._cache.pop(provider_slug, None)

    def clear(self) -> None:
        self._cache.clear()

    def get_all_datasets(self) -> list[Dataset]:
        result = []
        for entry in self._cache.values():
            if not entry.is_expired:
                result.extend(entry.datasets)
        return result
