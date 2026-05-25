"""Tests for batch/multi-geometry extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cas.core.models import (
    AggregationMethod,
    AttributeResult,
    BatchAttributeRequest,
    Geometry,
    QualityFlag,
)
from cas.extract.engine import batch_extract


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
    mock_instance = AsyncMock()
    mock_instance.extract = AsyncMock(return_value=mock_result)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls


class TestBatchExtract:
    @pytest.mark.asyncio
    async def test_multi_geometry_extraction(self, mock_result):
        mock_cls = _make_mock_connector(mock_result)
        geometries = [
            Geometry(type="Polygon", coordinates=[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]),
            Geometry(type="Polygon", coordinates=[[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]),
            Geometry(type="Point", coordinates=[-96.55, 39.05]),
        ]

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = BatchAttributeRequest(
                geometries=geometries,
                dataset_ids=["test_provider:test_var"],
            )
            response = await batch_extract(request)

        assert response.total_geometries == 3
        assert len(response.responses) == 3
        assert response.total_results == 3
        assert response.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_multi_geometry_multi_dataset(self, mock_result):
        mock_cls = _make_mock_connector(mock_result)
        geometries = [
            Geometry(type="Polygon", coordinates=[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]),
            Geometry(type="Polygon", coordinates=[[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]),
        ]

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = BatchAttributeRequest(
                geometries=geometries,
                dataset_ids=["test_provider:var_a", "test_provider:var_b"],
            )
            response = await batch_extract(request)

        assert response.total_geometries == 2
        assert len(response.responses) == 2
        for resp in response.responses:
            assert len(resp.results) == 2

    @pytest.mark.asyncio
    async def test_partial_failure_isolates_errors(self):
        call_count = 0

        async def failing_extract(dataset_id, geometry, time_range=None):
            nonlocal call_count
            call_count += 1
            if geometry.coordinates[0][0][0] == 0:
                raise Exception("Provider down for this region")
            return AttributeResult(
                dataset_id=dataset_id,
                variable="test_var",
                value=42.0,
                units="m",
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.GOOD,
                coverage_fraction=0.95,
                pixel_count=100,
                provider="test_provider",
            )

        mock_instance = AsyncMock()
        mock_instance.extract = AsyncMock(side_effect=failing_extract)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_cls = MagicMock(return_value=mock_instance)

        geometries = [
            Geometry(type="Polygon", coordinates=[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]),
            Geometry(type="Polygon", coordinates=[[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]),
        ]

        with (
            patch("cas.extract.engine.discover"),
            patch("cas.extract.engine.get_connector", return_value=mock_cls),
        ):
            request = BatchAttributeRequest(
                geometries=geometries,
                dataset_ids=["test_provider:test_var"],
            )
            response = await batch_extract(request)

        assert response.total_geometries == 2
        assert len(response.responses) == 2
        failed = response.responses[0]
        assert len(failed.warnings) > 0
        succeeded = response.responses[1]
        assert len(succeeded.results) == 1


class TestBatchAttributeRequest:
    def test_rejects_empty_geometries(self):
        with pytest.raises(ValueError):
            BatchAttributeRequest(
                geometries=[],
                dataset_ids=["test:var"],
            )

    def test_accepts_mixed_types(self):
        req = BatchAttributeRequest(
            geometries=[
                Geometry(type="Point", coordinates=[-96.55, 39.05]),
                Geometry(type="Polygon", coordinates=[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]),
            ],
            dataset_ids=["test:var"],
        )
        assert len(req.geometries) == 2
