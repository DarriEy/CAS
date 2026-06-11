"""Tests for the blessed public Python API facade (`import cas`)."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import cas
from cas.core.models import AggregationMethod, AttributeResult, QualityFlag


@pytest.fixture
def _clean_settings():
    """Snapshot/restore programmatic setting overrides around a test."""
    from cas.core.config import _overrides, get_settings

    saved = dict(_overrides)
    yield
    _overrides.clear()
    _overrides.update(saved)
    get_settings.cache_clear()


def _make_mock_connector(mock_result):
    """Mock connector class usable as `async with cls() as conn:` (as in test_engine)."""
    mock_instance = AsyncMock()
    mock_instance.extract = AsyncMock(return_value=mock_result)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_instance)


@pytest.fixture
def mock_result():
    return AttributeResult(
        dataset_id="test_provider:test_var",
        variable="test_var",
        value=42.0,
        units="m",
        aggregation=AggregationMethod.MEAN,
        quality=QualityFlag.GOOD,
        coverage_fraction=0.95,
        pixel_count=100,
        provider="test_provider",
        elapsed_ms=50,
    )


class TestFacadeSurface:
    def test_all_names_resolve(self):
        missing = [name for name in cas.__all__ if not hasattr(cas, name)]
        assert not missing

    def test_blessed_names_present(self):
        expected = {
            "extract", "batch_extract", "extract_sync", "batch_extract_sync",
            "configure", "discover", "list_providers", "get_connector",
            "AttributeRequest", "BatchAttributeRequest",
            "AttributeResponse", "BatchAttributeResponse",
            "AttributeResult", "QualityFlag",
        }
        assert expected <= set(cas.__all__)

    def test_version_is_pep440ish(self):
        assert cas.__version__.count(".") == 2

    def test_import_does_not_load_connectors(self):
        """`import cas` must stay light: connector modules load on discover()."""
        code = (
            "import sys, cas; "
            "leaked = [m for m in sys.modules if m.startswith('cas.connectors')]; "
            "sys.exit(1 if leaked else 0)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
        assert proc.returncode == 0, proc.stderr.decode()


class TestSyncWrappers:
    def test_extract_sync_round_trip(self, sample_geometry, mock_result):
        mock_cls = _make_mock_connector(mock_result)
        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = cas.AttributeRequest(
                geometry=sample_geometry,
                dataset_ids=["test_provider:test_var"],
            )
            response = cas.extract_sync(request)

        assert isinstance(response, cas.AttributeResponse)
        assert len(response.results) == 1
        assert response.results[0].value == 42.0
        assert response.results[0].quality == cas.QualityFlag.GOOD

    def test_batch_extract_sync_round_trip(self, sample_geometry, mock_result):
        mock_cls = _make_mock_connector(mock_result)
        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = cas.BatchAttributeRequest(
                geometries=[sample_geometry, sample_geometry],
                dataset_ids=["test_provider:test_var"],
            )
            batch = cas.batch_extract_sync(request)

        assert isinstance(batch, cas.BatchAttributeResponse)
        assert batch.total_geometries == 2
        assert batch.total_results == 2

    async def test_extract_sync_rejects_running_loop(self, sample_request):
        with pytest.raises(RuntimeError, match="running event loop"):
            cas.extract_sync(sample_request)

    async def test_batch_extract_sync_rejects_running_loop(self, sample_geometry):
        request = cas.BatchAttributeRequest(
            geometries=[sample_geometry], dataset_ids=["p:a"],
        )
        with pytest.raises(RuntimeError, match="running event loop"):
            cas.batch_extract_sync(request)


class TestConfigure:
    def test_configure_changes_setting(self, _clean_settings):
        from cas.core.config import get_settings

        settings = cas.configure(provider_timeout_s=7.5)
        assert settings.provider_timeout_s == 7.5
        assert get_settings().provider_timeout_s == 7.5

    def test_configure_rebuilds_result_cache(self, _clean_settings):
        from cas.extract.engine import get_result_cache

        cas.configure(result_cache_ttl_s=123.0, result_cache_max_entries=5)
        cache = get_result_cache()
        assert cache._default_ttl == 123.0
        assert cache._max_entries == 5

    def test_configure_no_args_rereads_environment(self, _clean_settings, monkeypatch):
        from cas.core.config import get_settings

        get_settings()  # prime the cache before the env var exists
        monkeypatch.setenv("CAS_MAX_DATASETS_PER_REQUEST", "3")
        assert get_settings().max_datasets_per_request != 3  # cached → env ignored
        assert cas.configure().max_datasets_per_request == 3

    def test_configure_overrides_beat_environment(self, _clean_settings, monkeypatch):
        monkeypatch.setenv("CAS_PROVIDER_TIMEOUT_S", "11")
        assert cas.configure(provider_timeout_s=22.0).provider_timeout_s == 22.0

    def test_configure_rejects_unknown_setting(self, _clean_settings):
        with pytest.raises(TypeError, match="Unknown CAS setting"):
            cas.configure(not_a_real_setting=1)

    def test_configure_validates_values(self, _clean_settings):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            cas.configure(provider_timeout_s="not-a-number")
