# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Continent-wide Africa connectors served by Digital Earth Africa (DEA).

DEA Africa (https://ows.digitalearth.africa) is the African analysis-ready
cloud-data platform — the regional analogue of Microsoft Planetary Computer.
Its OWS endpoint exposes a stable WCS catalogue; every ``coverage_id`` below was
taken verbatim from the live ``GetCapabilities`` response.

These layers add genuinely new attribute categories for Africa (cropland extent,
NDVI climatology, mangrove extent) rather than duplicating the global datasets
that already cover the continent. ``crop_mask`` and ``ndvi_climatology_ls`` are
served on DEA's equal-area EPSG:6933 grid (x/y axes) while ``gmw`` is EPSG:4326
(lat/lon) — each connector's ``crs`` is set to its DescribeCoverage value, and
the time-axis coverages carry a ``default_time`` matching an available slice.

Mangroves only exist on coastlines, so ``dea_africa_mangroves`` sets a
``health_anchor`` at the Rufiji Delta (Tanzania); without it the coverage-aware
health geometry picks an inland anchor where the server returns WCS 400 and the
provider reads as ``down``. (``dem_srtm`` was also evaluated and dropped — it
would only duplicate the existing global DEMs.)

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


# ── Mangrove extent (Global Mangrove Watch via DEA) ──
@register("dea_africa_mangroves")
class DEAAfricaMangrovesConnector(NationalWCSConnector):
    slug = "dea_africa_mangroves"
    display_name = "DEA Africa Mangroves"
    base_url = _DEA_BASE
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_mangroves",
        display_name="Digital Earth Africa Mangroves (Global Mangrove Watch)",
        wcs_url=_DEA_WCS,
        coverage_id="gmw",
        protocol_version="2.0.1",
        variable=Variable(
            name="mangrove_extent",
            units="class",
            data_type=DataType.CATEGORICAL,
            description="Mangrove presence (Global Mangrove Watch via DEA Africa)",
        ),
        resolution_m=25,
        category="vegetation",
        bbox=_AFRICA_BBOX,
        license="CC-BY 4.0",
        citation="Bunting et al., Global Mangrove Watch; via Digital Earth Africa",
        crs="EPSG:4326",
        default_time="2017-01-01",
        # Mangroves are coastal; sample the Rufiji Delta (Tanzania) where the
        # coverage actually has data, not the generic inland land anchor.
        health_anchor=(39.35, -7.90),
    )


_africa_placeholder = True
