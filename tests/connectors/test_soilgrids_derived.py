# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the ISRIC SoilGrids derived-layers connector (direct COG read)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from cas.connectors.soilgrids_derived import (
    WRB_CLASSES,
    SoilGridsDerivedConnector,
)
from cas.core.models import AggregationMethod, Geometry, QualityFlag


@pytest.fixture
def test_geometry() -> Geometry:
    return Geometry(
        type="Polygon",
        coordinates=[[
            [-93.7, 41.9],
            [-93.6, 41.9],
            [-93.6, 42.0],
            [-93.7, 42.0],
            [-93.7, 41.9],
        ]],
    )


class TestSoilGridsDerivedConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with SoilGridsDerivedConnector() as conn:
            datasets = await conn.list_datasets()
        ids = {d.id for d in datasets}
        assert ids == {"soilgrids_derived:ocs", "soilgrids_derived:wrb_class"}
        assert all(d.resolution_m == 250 for d in datasets)

    @pytest.mark.asyncio
    async def test_extract_ocs_mean(self, test_geometry):
        # OCS stock (t/ha) — continuous, MEAN aggregation. -32768 is nodata.
        mock_raster = np.array(
            [[60, 70, 65], [68, 72, 60], [-32768, 64, 66]], dtype=np.int16
        )
        from rasterio.transform import from_bounds
        mock_transform = from_bounds(-93.7, 41.9, -93.6, 42.0, 3, 3)

        with patch.object(
            SoilGridsDerivedConnector, "_read_cog_window", new_callable=AsyncMock,
            return_value=(mock_raster, mock_transform, -32768.0, "EPSG:4326"),
        ):
            async with SoilGridsDerivedConnector() as conn:
                result = await conn.extract(
                    dataset_id="soilgrids_derived:ocs", geometry=test_geometry,
                )

        assert result.variable == "ocs"
        assert result.units == "t/ha"
        assert result.aggregation == AggregationMethod.MEAN
        assert isinstance(result.value, float)
        # Mean of valid pixels (nodata excluded) lands in the expected range.
        assert 60 <= result.value <= 72
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_wrb_class_distribution_and_mapping(self, test_geometry):
        # WRB codes: 7=Chernozems, 18=Luvisols, 20=Phaeozems, 255=nodata.
        mock_raster = np.array(
            [[7, 7, 20], [18, 20, 20], [255, 7, 18]], dtype=np.uint8
        )
        from rasterio.transform import from_bounds
        mock_transform = from_bounds(-93.7, 41.9, -93.6, 42.0, 3, 3)

        with patch.object(
            SoilGridsDerivedConnector, "_read_cog_window", new_callable=AsyncMock,
            return_value=(mock_raster, mock_transform, 255.0, "EPSG:4326"),
        ):
            async with SoilGridsDerivedConnector() as conn:
                result = await conn.extract(
                    dataset_id="soilgrids_derived:wrb_class", geometry=test_geometry,
                )

        assert result.variable == "wrb_class"
        assert result.aggregation == AggregationMethod.DISTRIBUTION
        assert isinstance(result.value, dict)
        assert "Chernozems" in result.value
        assert "Luvisols" in result.value
        assert "Phaeozems" in result.value
        # nodata code 255 must not surface as a class.
        assert "class_255" not in result.value
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)

    @pytest.mark.asyncio
    async def test_extract_unknown_layer_raises(self, test_geometry):
        from cas.core.exceptions import DataFormatError

        async with SoilGridsDerivedConnector() as conn:
            with pytest.raises(DataFormatError):
                await conn.extract(
                    dataset_id="soilgrids_derived:bogus", geometry=test_geometry,
                )

    def test_wrb_classes(self):
        assert WRB_CLASSES[0] == "Acrisols"
        assert WRB_CLASSES[14] == "Histosols"
        assert WRB_CLASSES[29] == "Vertisols"
