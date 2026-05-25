"""Tests for Copernicus DEM connector."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from cas.connectors.copernicus_dem import CopernicusDEMConnector
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
        "id": "Copernicus_DSM_COG_10_N39_00_W097_00_DEM",
        "type": "Feature",
        "assets": {
            "data": {
                "href": "https://example.com/mock_dem.tif",
            }
        },
    }
]


class TestCopernicusDEMConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with CopernicusDEMConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].id == "copernicus_dem:elevation"
        assert datasets[0].resolution_m == 30

    @pytest.mark.asyncio
    async def test_extract_elevation(self, test_geometry):
        mock_raster = np.array(
            [[400, 405, 410], [395, 400, 405], [390, 395, 400]],
            dtype=np.float32,
        )
        from rasterio.transform import from_bounds

        mock_transform = from_bounds(-96.6, 39.0, -96.5, 39.1, 3, 3)

        with (
            patch.object(
                CopernicusDEMConnector,
                "_stac_search",
                new_callable=AsyncMock,
                return_value=MOCK_STAC_RESPONSE,
            ),
            patch.object(
                CopernicusDEMConnector,
                "_read_cog_window",
                new_callable=AsyncMock,
                return_value=(mock_raster, mock_transform, -9999.0, "EPSG:4326"),
            ),
        ):
            async with CopernicusDEMConnector() as conn:
                result = await conn.extract(
                    dataset_id="copernicus_dem:elevation",
                    geometry=test_geometry,
                )

        assert result.variable == "elevation"
        assert result.units == "m"
        assert result.value is not None
        assert 390 < result.value < 410
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_no_items(self, test_geometry):
        with patch.object(
            CopernicusDEMConnector,
            "_stac_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with CopernicusDEMConnector() as conn:
                result = await conn.extract(
                    dataset_id="copernicus_dem:elevation",
                    geometry=test_geometry,
                )

        assert result.quality == QualityFlag.MISSING
        assert result.value is None
