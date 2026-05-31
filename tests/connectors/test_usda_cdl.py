# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the USDA CDL connector (Planetary Computer STAC+COG)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from cas.connectors.usda_cdl import USDAcroplandConnector
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


# The collection returns three item types per tile/year; only the cropland_*
# item carries the "cropland" COG asset.
MOCK_ITEMS = [
    {"id": "cultivated_2021_x", "assets": {"cultivated": {"href": "https://e/c.tif"}}},
    {"id": "cropland_2021_x", "assets": {"cropland": {"href": "https://e/cdl.tif"}}},
    {"id": "frequency_2008-2021_x", "assets": {"corn": {"href": "https://e/f.tif"}}},
]


class TestUSDACDLConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with USDAcroplandConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].id == "usda_cdl:cropland"
        assert datasets[0].resolution_m == 30
        assert datasets[0].crs == "EPSG:5070"

    @pytest.mark.asyncio
    async def test_extract_picks_cropland_item_and_maps_classes(self, test_geometry):
        # CDL class codes: 1=Corn, 5=Soybeans, 176=Grassland/pasture, 0=background.
        mock_raster = np.array([[1, 5, 176], [176, 176, 5], [0, 176, 1]], dtype=np.uint8)
        from rasterio.transform import from_bounds
        mock_transform = from_bounds(-96.6, 39.0, -96.5, 39.1, 3, 3)

        with (
            patch.object(USDAcroplandConnector, "_stac_search",
                         new_callable=AsyncMock, return_value=MOCK_ITEMS),
            # CRS faked as 4326 to match the degree-based mock transform; real 5070
            # reprojection is covered by the live network test.
            patch.object(USDAcroplandConnector, "_read_cog_window", new_callable=AsyncMock,
                         return_value=(mock_raster, mock_transform, None, "EPSG:4326")),
            patch.object(USDAcroplandConnector, "_sign_planetary_computer",
                         side_effect=lambda h: h),
        ):
            async with USDAcroplandConnector() as conn:
                result = await conn.extract(dataset_id="usda_cdl:cropland", geometry=test_geometry)

        assert result.variable == "cropland"
        assert isinstance(result.value, dict)
        # Codes are mapped to human-readable class names.
        assert "Corn" in result.value and "Soybeans" in result.value
        assert "Grassland/pasture" in result.value
        # Background (0) is treated as nodata, not a class.
        assert "crop_0" not in result.value
        # Provenance references the cropland item, not cultivated/frequency.
        assert "cropland_2021_x" in result.provenance
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_no_cropland_items(self, test_geometry):
        # Only non-cropland items intersect → both searches return no cropland asset.
        non_cropland = [{"id": "cultivated_2021_x", "assets": {"cultivated": {"href": "h"}}}]
        with patch.object(USDAcroplandConnector, "_stac_search",
                          new_callable=AsyncMock, return_value=non_cropland):
            async with USDAcroplandConnector() as conn:
                result = await conn.extract(dataset_id="usda_cdl:cropland", geometry=test_geometry)

        assert result.quality == QualityFlag.MISSING
        assert result.value is None
