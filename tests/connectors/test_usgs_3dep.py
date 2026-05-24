"""Tests for USGS 3DEP connector."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from cas.connectors.usgs_3dep import USGS3DEPConnector
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
        "id": "n40w097-13",
        "type": "Feature",
        "assets": {
            "data": {
                "href": "https://example.com/mock_3dep.tif",
            }
        },
    }
]


class TestUSGS3DEPConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with USGS3DEPConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].id == "usgs_3dep:elevation"
        assert datasets[0].resolution_m == 10

    @pytest.mark.asyncio
    async def test_extract_elevation(self, test_geometry):
        mock_raster = np.array(
            [[420, 425, 430], [415, 420, 425], [410, 415, 420]],
            dtype=np.float32,
        )
        from rasterio.transform import from_bounds

        mock_transform = from_bounds(-96.6, 39.0, -96.5, 39.1, 3, 3)

        with (
            patch.object(
                USGS3DEPConnector,
                "_stac_search",
                new_callable=AsyncMock,
                return_value=MOCK_STAC_RESPONSE,
            ),
            patch.object(
                USGS3DEPConnector,
                "_read_cog_window",
                new_callable=AsyncMock,
                return_value=(mock_raster, mock_transform, -9999.0),
            ),
        ):
            async with USGS3DEPConnector() as conn:
                result = await conn.extract(
                    dataset_id="usgs_3dep:elevation",
                    geometry=test_geometry,
                )

        assert result.variable == "elevation"
        assert result.units == "m"
        assert result.value is not None
        assert 410 < result.value < 430
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_no_items(self, test_geometry):
        with patch.object(
            USGS3DEPConnector,
            "_stac_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with USGS3DEPConnector() as conn:
                result = await conn.extract(
                    dataset_id="usgs_3dep:elevation",
                    geometry=test_geometry,
                )

        assert result.quality == QualityFlag.MISSING
        assert result.value is None
