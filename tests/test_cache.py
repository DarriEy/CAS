"""Tests for metadata cache."""

from __future__ import annotations

from cas.cache.metadata import MetadataCache


class TestMetadataCache:
    def test_set_and_get(self, sample_dataset):
        cache = MetadataCache()
        cache.set("test_provider", [sample_dataset])
        result = cache.get("test_provider")
        assert result is not None
        assert len(result) == 1
        assert result[0].id == "test_provider:test_var"

    def test_get_missing_returns_none(self):
        cache = MetadataCache()
        assert cache.get("nonexistent") is None

    def test_invalidate(self, sample_dataset):
        cache = MetadataCache()
        cache.set("test_provider", [sample_dataset])
        cache.invalidate("test_provider")
        assert cache.get("test_provider") is None

    def test_clear(self, sample_dataset):
        cache = MetadataCache()
        cache.set("a", [sample_dataset])
        cache.set("b", [sample_dataset])
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_get_all_datasets(self, sample_dataset):
        cache = MetadataCache()
        cache.set("a", [sample_dataset])
        cache.set("b", [sample_dataset])
        all_ds = cache.get_all_datasets()
        assert len(all_ds) == 2

    def test_expired_entry_returns_none(self, sample_dataset):
        cache = MetadataCache(default_ttl=0)
        cache.set("test_provider", [sample_dataset])
        # TTL=0 means immediately expired
        import time
        time.sleep(0.01)
        assert cache.get("test_provider") is None
