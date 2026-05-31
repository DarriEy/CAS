# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Continent-wide Africa connectors served by Digital Earth Africa (DEA).

DEA Africa (https://ows.digitalearth.africa) is the African analysis-ready
cloud-data platform — the regional analogue of Microsoft Planetary Computer.
Its OWS endpoint exposes a stable WCS catalogue; every ``coverage_id`` below was
taken verbatim from the live ``GetCapabilities`` response.

These layers add genuinely new attribute categories for Africa (cropland extent
and NDVI climatology) rather than duplicating the global datasets that already
cover the continent. Both coverages are served on DEA's equal-area EPSG:6933
grid (x/y axes), and each carries a ``time`` axis so a ``default_time`` matching
the coverage's DescribeCoverage is required.

Two other DEA coverages were evaluated and deferred:
- ``dem_srtm`` — would only duplicate the existing global DEMs.
- ``gmw`` (Global Mangrove Watch) — extracts correctly at coastal points (e.g.
  the Rufiji/Niger/Bijagos deltas) but the coverage-aware health geometry picks
  an inland anchor where the server returns WCS 400, so it reads as ``down`` in
  the sweep. Needs a coastal-aware test anchor (or graceful 400→empty handling)
  before it can ship without tripping health-regression alerts.

The four pre-existing DEA connectors (``dea_africa_dem`` slope, ``dea_africa_fc``,
``dea_africa_lc``, ``dea_africa_water``) live in ``national_europe.py`` for
historical reasons; new DEA layers are collected here. Discovery auto-imports
every module in this package, so the ``@register`` decorators are the only wiring
required.
"""

from __future__ import annotations

from cas.connectors.national_wcs import NationalDatasetConfig, NationalWCSConnector
from cas.core.models import BoundingBox, DataType, Variable
from cas.core.registry import register

# Whole-continent extent, matching the existing DEA Africa connectors.
_AFRICA_BBOX = BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38)
_DEA_WCS = "https://ows.digitalearth.africa/wcs"
_DEA_BASE = "https://ows.digitalearth.africa"


# ── Cropland extent (DEA crop mask, Eastern Africa coverage) ──
@register("dea_africa_cropland")
class DEAAfricaCroplandConnector(NationalWCSConnector):
    slug = "dea_africa_cropland"
    display_name = "DEA Africa Cropland Extent"
    base_url = _DEA_BASE
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_cropland",
        display_name="Digital Earth Africa Cropland Extent Map",
        wcs_url=_DEA_WCS,
        coverage_id="crop_mask",
        protocol_version="2.0.1",
        variable=Variable(
            name="cropland",
            units="class",
            data_type=DataType.CATEGORICAL,
            description="Cropland / non-cropland extent (DEA Africa)",
        ),
        resolution_m=10,
        category="land_cover",
        bbox=_AFRICA_BBOX,
        license="CC-BY 4.0",
        citation="Digital Earth Africa, Cropland Extent Map",
        crs="EPSG:6933",
        default_time="2019-01-01",
    )


# ── NDVI climatology (long-term vegetation greenness) ──
@register("dea_africa_ndvi")
class DEAAfricaNDVIConnector(NationalWCSConnector):
    slug = "dea_africa_ndvi"
    display_name = "DEA Africa NDVI Climatology"
    base_url = _DEA_BASE
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_ndvi",
        display_name="Digital Earth Africa NDVI Climatology (Landsat)",
        wcs_url=_DEA_WCS,
        coverage_id="ndvi_climatology_ls",
        protocol_version="2.0.1",
        variable=Variable(
            name="ndvi",
            units="index",
            data_type=DataType.CONTINUOUS,
            valid_range=(-1, 1),
            description="Long-term mean NDVI (DEA Africa, Landsat)",
        ),
        resolution_m=30,
        category="vegetation",
        bbox=_AFRICA_BBOX,
        license="CC-BY 4.0",
        citation="Digital Earth Africa, NDVI Climatology (Landsat)",
        crs="EPSG:6933",
        default_time="1984-01-01",
    )


_africa_placeholder = True
