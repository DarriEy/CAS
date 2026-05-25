"""Tests for ESA WorldCover connector."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from cas.connectors.esa_worldcover import WORLDCOVER_CLASSES, ESAWorldCoverConnector
from cas.core.models import Geometry, QualityFlag


@pytest.fixture
def test_geometry() -> Geometry:
    return Geometry(
        type="Polygon",
        coordinates=[[
            [-96.6, 39.0],
            [-96.5, 39.0],
            [-96.5, 39.1],
            [-96.6, 39.1],
            [-96.6, 39.0],
        ]],
    )


MOCK_STAC_RESPONSE = [
    {
        "id": "ESA_WorldCover_10m_2021_v200_N39W097",
        "type": "Feature",
        "assets": {
            "map": {
                "href": "https://example.com/mock_worldcover.tif",
            }
        },
    }
]


class TestESAWorldCoverConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with ESAWorldCoverConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].id == "esa_worldcover:land_cover"
        assert datasets[0].resolution_m == 10

    @pytest.mark.asyncio
    async def test_extract_land_cover(self, test_geometry):
        # Categorical raster: mostly cropland (40) and grassland (30)
        mock_raster = np.array(
            [[40, 40, 30], [40, 40, 30], [30, 30, 10]],
            dtype=np.uint8,
        )
        from rasterio.transform import from_bounds

        mock_transform = from_bounds(-96.6, 39.0, -96.5, 39.1, 3, 3)

        with (
            patch.object(
                ESAWorldCoverConnector,
                "_stac_search",
                new_callable=AsyncMock,
                return_value=MOCK_STAC_RESPONSE,
            ),
            patch.object(
                ESAWorldCoverConnector,
                "_read_cog_window",
                new_callable=AsyncMock,
                return_value=(mock_raster, mock_transform, 0.0, "EPSG:4326"),
            ),
        ):
            async with ESAWorldCoverConnector() as conn:
                result = await conn.extract(
                    dataset_id="esa_worldcover:land_cover",
                    geometry=test_geometry,
                )

        assert result.variable == "land_cover"
        assert isinstance(result.value, dict)
        assert "Cropland" in result.value
        assert "Grassland" in result.value
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_no_items(self, test_geometry):
        with patch.object(
            ESAWorldCoverConnector,
            "_stac_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with ESAWorldCoverConnector() as conn:
                result = await conn.extract(
                    dataset_id="esa_worldcover:land_cover",
                    geometry=test_geometry,
                )

        assert result.quality == QualityFlag.MISSING

    def test_worldcover_classes(self):
        assert WORLDCOVER_CLASSES[10] == "Tree cover"
        assert WORLDCOVER_CLASSES[40] == "Cropland"
        assert WORLDCOVER_CLASSES[80] == "Permanent water bodies"
