# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the IO 10m LULC 9-class connector (Planetary Computer STAC+COG)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from cas.connectors.io_lulc_9class import IO9_CLASSES, IOLULC9ClassConnector
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


MOCK_ITEMS = [
    {"id": "20-2020", "assets": {"data": {"href": "https://e/2020.tif"}}},
    {"id": "20-2021", "assets": {"data": {"href": "https://e/2021.tif"}}},
]


class TestIOLULC9ClassConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with IOLULC9ClassConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].id == "io_lulc_9class:land_cover"
        assert datasets[0].resolution_m == 10

    @pytest.mark.asyncio
    async def test_extract_maps_classes_and_picks_latest(self, test_geometry):
        # 5=Crops, 11=Rangeland, 2=Trees, 0=No data (treated as nodata).
        mock_raster = np.array([[5, 5, 11], [11, 11, 2], [0, 11, 5]], dtype=np.uint8)
        from rasterio.transform import from_bounds
        mock_transform = from_bounds(-96.6, 39.0, -96.5, 39.1, 3, 3)

        with (
            patch.object(IOLULC9ClassConnector, "_stac_search",
                         new_callable=AsyncMock, return_value=MOCK_ITEMS),
            patch.object(IOLULC9ClassConnector, "_read_cog_window", new_callable=AsyncMock,
                         return_value=(mock_raster, mock_transform, 0.0, "EPSG:4326")),
            patch.object(IOLULC9ClassConnector, "_sign_planetary_computer",
                         side_effect=lambda h: h),
        ):
            async with IOLULC9ClassConnector() as conn:
                result = await conn.extract(
                    dataset_id="io_lulc_9class:land_cover", geometry=test_geometry,
                )

        assert result.variable == "land_cover"
        assert isinstance(result.value, dict)
        assert "Crops" in result.value
        assert "Rangeland" in result.value
        assert "Trees" in result.value
        # The latest item (2021) is chosen deterministically.
        assert "20-2021" in result.provenance
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_no_items(self, test_geometry):
        with patch.object(IOLULC9ClassConnector, "_stac_search",
                          new_callable=AsyncMock, return_value=[]):
            async with IOLULC9ClassConnector() as conn:
                result = await conn.extract(
                    dataset_id="io_lulc_9class:land_cover", geometry=test_geometry,
                )
        assert result.quality == QualityFlag.MISSING
        assert result.value is None

    def test_io9_classes(self):
        assert IO9_CLASSES[1] == "Water"
        assert IO9_CLASSES[5] == "Crops"
        assert IO9_CLASSES[11] == "Rangeland"
