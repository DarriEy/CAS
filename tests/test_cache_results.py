"""Tests for result cache."""

from __future__ import annotations

import time

from cas.cache.results import ResultCache
from cas.core.models import AggregationMethod, TimeRange


class TestResultCache:
    def test_set_and_get(self, sample_result):
        cache = ResultCache()
        key = cache.make_key("ds:var", "abc123", AggregationMethod.MEAN, None)
        cache.set(key, sample_result)
        assert cache.get(key) is not None
        assert cache.get(key).value == 245.0

    def test_get_missing_returns_none(self):
        cache = ResultCache()
        assert cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self, sample_result):
        cache = ResultCache(default_ttl=0)
        key = "test_key"
        cache.set(key, sample_result)
        time.sleep(0.01)
        assert cache.get(key) is None

    def test_lru_eviction(self, sample_result):
        cache = ResultCache(max_entries=3)
        cache.set("a", sample_result)
        cache.set("b", sample_result)
        cache.set("c", sample_result)
        cache.set("d", sample_result)
        assert cache.get("a") is None
        assert cache.get("b") is not None
        assert cache.get("d") is not None

    def test_lru_access_refreshes(self, sample_result):
        cache = ResultCache(max_entries=3)
        cache.set("a", sample_result)
        cache.set("b", sample_result)
        cache.get("a")  # refresh a
        cache.set("c", sample_result)
        cache.set("d", sample_result)  # should evict b (oldest untouched)
        assert cache.get("a") is not None
        assert cache.get("b") is None

    def test_make_key_deterministic(self):
        k1 = ResultCache.make_key("ds:var", "hash1", AggregationMethod.MEAN, None)
        k2 = ResultCache.make_key("ds:var", "hash1", AggregationMethod.MEAN, None)
        assert k1 == k2

    def test_make_key_differs_by_aggregation(self):
        k1 = ResultCache.make_key("ds:var", "hash1", AggregationMethod.MEAN, None)
        k2 = ResultCache.make_key("ds:var", "hash1", AggregationMethod.MEDIAN, None)
        assert k1 != k2

    def test_make_key_differs_by_time_range(self):
        from datetime import datetime

        tr = TimeRange(start=datetime(2020, 1, 1), end=datetime(2020, 12, 31))
        k1 = ResultCache.make_key("ds:var", "hash1", AggregationMethod.MEAN, None)
        k2 = ResultCache.make_key("ds:var", "hash1", AggregationMethod.MEAN, tr)
        assert k1 != k2

    def test_stats_tracking(self, sample_result):
        cache = ResultCache()
        cache.set("a", sample_result)
        cache.get("a")
        cache.get("b")

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["hit_rate"] == 0.5

    def test_clear_resets_stats(self, sample_result):
        cache = ResultCache()
        cache.set("a", sample_result)
        cache.get("a")
        cache.clear()
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["size"] == 0
