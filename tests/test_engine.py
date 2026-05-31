"""Tests for the extraction engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cas.core.models import (
    AggregationMethod,
    AttributeRequest,
    AttributeResult,
    BatchAttributeRequest,
    QualityFlag,
)
from cas.extract.engine import batch_extract, extract


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


def _make_mock_connector(mock_result):
    """Create a mock connector class that works with `async with cls() as conn:`."""
    mock_instance = AsyncMock()
    mock_instance.extract = AsyncMock(return_value=mock_result)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)

    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


class TestExtractEngine:
    @pytest.mark.asyncio
    async def test_single_dataset_extraction(self, sample_geometry, mock_result):
        mock_cls, _ = _make_mock_connector(mock_result)

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = AttributeRequest(
                geometry=sample_geometry,
                dataset_ids=["test_provider:test_var"],
            )
            response = await extract(request)

        assert len(response.results) == 1
        assert response.results[0].value == 42.0
        assert response.elapsed_ms >= 0
        assert response.request_id

    @pytest.mark.asyncio
    async def test_multi_dataset_extraction(self, sample_geometry, mock_result):
        mock_cls, _ = _make_mock_connector(mock_result)

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = AttributeRequest(
                geometry=sample_geometry,
                dataset_ids=["test_provider:var_a", "test_provider:var_b"],
            )
            response = await extract(request)

        assert len(response.results) == 2

    @pytest.mark.asyncio
    async def test_cross_provider_consistency_flags_divergence(self, sample_geometry):
        """Two providers answering the same variable with divergent values
        should surface a cross-provider consistency warning."""
        def _elev_result(provider, value):
            return AttributeResult(
                dataset_id=f"{provider}:elevation",
                variable="elevation",
                value=value,
                units="m",
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.GOOD,
                coverage_fraction=0.95,
                pixel_count=100,
                provider=provider,
                elapsed_ms=50,
            )

        connectors = {
            "dem_a": _make_mock_connector(_elev_result("dem_a", 100.0))[0],
            "dem_b": _make_mock_connector(_elev_result("dem_b", 1000.0))[0],
        }

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", side_effect=connectors.get),
        ):
            request = AttributeRequest(
                geometry=sample_geometry,
                dataset_ids=["dem_a:elevation", "dem_b:elevation"],
            )
            response = await extract(request)

        assert len(response.results) == 2
        assert any("cross-provider mean" in w for w in response.warnings)

    @pytest.mark.asyncio
    async def test_provider_timeout_becomes_warning(self, sample_geometry, monkeypatch):
        """A provider exceeding the per-provider deadline is a warning, not a crash."""
        import asyncio

        from cas.core.config import get_settings

        monkeypatch.setenv("CAS_PROVIDER_TIMEOUT_S", "0.05")
        get_settings.cache_clear()

        async def _slow_extract(*args, **kwargs):
            await asyncio.sleep(5)

        mock_instance = AsyncMock()
        mock_instance.extract = _slow_extract
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_cls = MagicMock(return_value=mock_instance)

        try:
            with (
                patch("cas.extract.engine.discover"),
                patch("cas.extract.engine.get_connector", return_value=mock_cls),
            ):
                request = AttributeRequest(
                    geometry=sample_geometry,
                    dataset_ids=["test_provider:slow_var"],
                )
                response = await extract(request)
        finally:
            get_settings.cache_clear()

        assert len(response.results) == 0
        assert any("timeout" in w for w in response.warnings)

    @pytest.mark.asyncio
    async def test_too_many_datasets_raises_request_limit(self, sample_geometry, monkeypatch):
        from cas.core.config import get_settings
        from cas.core.exceptions import RequestLimitError

        monkeypatch.setenv("CAS_MAX_DATASETS_PER_REQUEST", "2")
        get_settings.cache_clear()
        try:
            with patch("cas.extract.engine.discover"):
                request = AttributeRequest(
                    geometry=sample_geometry,
                    dataset_ids=["p:a", "p:b", "p:c"],
                )
                with pytest.raises(RequestLimitError):
                    await extract(request)
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_batch_rejects_over_limit_datasets(self, sample_geometry, monkeypatch):
        """An over-limit batch must raise (→ 422), not be swallowed into per-geometry warnings."""
        from cas.core.config import get_settings
        from cas.core.exceptions import RequestLimitError

        monkeypatch.setenv("CAS_MAX_DATASETS_PER_REQUEST", "2")
        get_settings.cache_clear()
        try:
            with patch("cas.extract.engine.discover"):
                request = BatchAttributeRequest(
                    geometries=[sample_geometry, sample_geometry],
                    dataset_ids=["p:a", "p:b", "p:c"],
                )
                with pytest.raises(RequestLimitError):
                    await batch_extract(request)
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_failed_extraction_produces_warning(self, sample_geometry):
        mock_instance = AsyncMock()
        mock_instance.extract = AsyncMock(side_effect=Exception("Provider down"))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_cls = MagicMock(return_value=mock_instance)

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = AttributeRequest(
                geometry=sample_geometry,
                dataset_ids=["test_provider:bad_var"],
            )
            response = await extract(request)

        assert len(response.results) == 0
        assert len(response.warnings) > 0
