# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Unit tests for the MapBiomas regional/country connectors."""

from __future__ import annotations

import pytest

from cas.connectors.mapbiomas import MAPBIOMAS_CLASSES
from cas.connectors.mapbiomas_regional import (
    CHACO_CLASSES,
    MapBiomasAmazoniaConnector,
    MapBiomasBoliviaConnector,
    MapBiomasChacoConnector,
    MapBiomasColombiaConnector,
    MapBiomasIndonesiaConnector,
    MapBiomasPampaConnector,
    MapBiomasParaguayConnector,
    MapBiomasPeruConnector,
    MapBiomasUruguayConnector,
    MapBiomasVenezuelaConnector,
)

network = pytest.mark.network

REGIONAL = [
    MapBiomasAmazoniaConnector,
    MapBiomasChacoConnector,
    MapBiomasPampaConnector,
    MapBiomasBoliviaConnector,
    MapBiomasColombiaConnector,
    MapBiomasPeruConnector,
    MapBiomasParaguayConnector,
    MapBiomasUruguayConnector,
    MapBiomasVenezuelaConnector,
    MapBiomasIndonesiaConnector,
]


class TestMapBiomasRegionalMetadata:
    @pytest.mark.parametrize("cls", REGIONAL)
    def test_metadata(self, cls):
        conn = cls()
        assert conn.slug.startswith("mapbiomas_")
        assert conn.protocol == "stac_cog"
        # The COG URL is region-specific and points at the public GCS bucket.
        assert conn.cog_url.startswith("https://storage.googleapis.com/mapbiomas-public/")
        assert conn.cog_url.endswith(".tif")

    @pytest.mark.parametrize("cls", REGIONAL)
    @pytest.mark.asyncio
    async def test_list_datasets(self, cls):
        conn = cls()
        datasets = await conn.list_datasets()
        assert len(datasets) == 1
        ds = datasets[0]
        assert ds.id == f"{conn.slug}:land_cover"
        assert ds.provider == conn.slug
        assert ds.variables[0].data_type.value == "categorical"
        assert ds.resolution_m == 30.0
        assert ds.crs == "EPSG:4326"
        assert ds.bbox.min_lon < ds.bbox.max_lon
        assert ds.bbox.min_lat < ds.bbox.max_lat


class TestMapBiomasRegionalLegend:
    """MapBiomas codes are NOT portable across initiatives — each connector
    must carry its own legend. These guard the per-region mapping so a future
    refactor can't silently collapse them back to one shared dict."""

    def test_chaco_uses_its_own_legend(self):
        assert MapBiomasChacoConnector.legend is CHACO_CLASSES

    def test_chaco_redefines_codes_vs_brazil(self):
        # The Chaco legend is NOT a superset of the Brazil one — codes carry
        # different meanings, so reusing one shared dict would mislabel data.
        # Code 43 = "Closed Grassland" and 57 = "Single crop" in Chaco; neither
        # carries that meaning in the Brazil-integrated dict (43 is absent there,
        # 57 is absent there), proving the two legends must stay separate.
        assert CHACO_CLASSES[43] == "closed_grassland"
        assert CHACO_CLASSES[57] == "single_crop"
        assert MAPBIOMAS_CLASSES.get(43) != CHACO_CLASSES[43]
        assert 57 not in MAPBIOMAS_CLASSES

    def test_amazonia_uses_integrated_legend(self):
        # Amazonia (RAISG) follows the integrated MapBiomas legend.
        assert MapBiomasAmazoniaConnector.legend is MAPBIOMAS_CLASSES


@network
class TestMapBiomasRegionalExtract:
    """Live windowed-COG extraction over a small polygon in each region's
    heartland. Marked ``network`` so it is excluded from the default CI run."""

    # Default to each connector's curated health anchor (verified on-data).
    SITES = {cls: cls.health_anchor for cls in REGIONAL}

    @staticmethod
    def _box(lon: float, lat: float, d: float = 0.05):
        from cas.core.models import Geometry

        return Geometry(
            type="Polygon",
            coordinates=[[
                [lon - d, lat - d],
                [lon + d, lat - d],
                [lon + d, lat + d],
                [lon - d, lat + d],
                [lon - d, lat - d],
            ]],
        )

    @pytest.mark.parametrize("cls", REGIONAL)
    @pytest.mark.asyncio
    async def test_extract_returns_distribution(self, cls):
        conn = cls()
        lon, lat = self.SITES[cls]
        result = await conn.extract(f"{conn.slug}:land_cover", self._box(lon, lat))
        assert result.coverage_fraction > 0.0
        assert isinstance(result.value, dict)
        # All keys must be resolved class names (or class_<n> fallbacks), and
        # the distribution must sum to ~1.
        assert abs(sum(result.value.values()) - 1.0) < 0.01
        assert all(isinstance(k, str) for k in result.value)
