# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""National WCS/WMS connectors — Asia, Oceania, and Africa."""

from __future__ import annotations

from cas.connectors.national_wcs import NationalDatasetConfig, NationalWCSConnector
from cas.core.models import BoundingBox, DataType, Variable
from cas.core.registry import register

ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 9000))


# ═══════════════════════════════════════════════════════════════════════
#  OCEANIA
# ═══════════════════════════════════════════════════════════════════════

@register("australia_dem")
class AustraliaDEMConnector(NationalWCSConnector):
    slug = "australia_dem"
    display_name = "Australia SRTM 1s DEM 30m"
    base_url = "https://services.ga.gov.au/gis/services/DEM_SRTM_1Second_2024/MapServer/WCSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_dem", display_name="Australia SRTM-1s DEM 30m (GA)",
        wcs_url="https://services.ga.gov.au/gis/services/DEM_SRTM_1Second_2024/MapServer/WCSServer",
        coverage_id="1", variable=ELEV_VAR, resolution_m=30,
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="Geoscience Australia, SRTM-derived 1 Second DEM",
    )


@register("australia_dea_lc")
class AustraliaDEALCConnector(NationalWCSConnector):
    slug = "australia_dea_lc"
    display_name = "Australia DEA Land Cover 25m"
    base_url = "https://ows.dea.ga.gov.au"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_dea_lc", display_name="Australia DEA Land Cover 25m (Landsat)",
        wcs_url="https://ows.dea.ga.gov.au",
        coverage_id="ga_ls_landcover_c3",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Annual land cover from Landsat (FAO LCCS v2, 1988-present)"),
        resolution_m=25, category="land_cover",
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="DEA, Geoscience Australia Land Cover",
        crs="EPSG:3577",
        default_time="2023-01-01T00:00:00Z",
    )


# ═══════════════════════════════════════════════════════════════════════
#  ASIA
# ═══════════════════════════════════════════════════════════════════════

# Disabled: NLSC's WMS does not serve true elevation. The configured
# `NLSC_DEM20m` layer returns a blank single-value GIF, and the only DEM-ish
# layer in GetCapabilities (`DDEM05`) returns RGBA shaded-relief imagery, not
# elevation values — so any "elevation" extracted here would be RGB bytes, not
# metres. No WCS/coverage endpoint exists. Verified 2026-05-30.
# Re-enable path: if NLSC publishes a real elevation WCS/COG (or a token-gated
# coverage), point coverage_id at it and restore @register.
# @register("taiwan_dem")
class TaiwanDEMConnector(NationalWCSConnector):
    slug = "taiwan_dem"
    display_name = "Taiwan DEM 20m"
    base_url = "https://wms.nlsc.gov.tw/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="taiwan_dem", display_name="Taiwan DTM 20m (NLSC)",
        wcs_url="https://wms.nlsc.gov.tw/wms",
        coverage_id="NLSC_DEM20m", variable=ELEV_VAR, resolution_m=20,
        bbox=BoundingBox(min_lon=119.9, min_lat=21.9, max_lon=122.1, max_lat=25.3),
        license="Open (NLSC)", citation="NLSC, National Land Surveying and Mapping Center",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════

@register("australia_water_obs")
class AustraliaWaterObsConnector(NationalWCSConnector):
    slug = "australia_water_obs"
    display_name = "Australia Water Observations (DEA)"
    base_url = "https://ows.dea.ga.gov.au"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_water_obs",
        display_name="Australia Water Observations from Space (DEA WOfS)",
        wcs_url="https://ows.dea.ga.gov.au",
        coverage_id="wofs_filtered_summary", protocol_version="2.0.1",
        variable=Variable(name="water_frequency", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Water presence frequency from Landsat — controls surface water dynamics"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="DEA, Water Observations from Space",
        crs="EPSG:3577",
    )


@register("australia_frac_cover")
class AustraliaFracCoverConnector(NationalWCSConnector):
    slug = "australia_frac_cover"
    display_name = "Australia Fractional Cover (DEA)"
    base_url = "https://ows.dea.ga.gov.au"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_frac_cover",
        display_name="Australia Fractional Cover 25m (DEA Landsat)",
        wcs_url="https://ows.dea.ga.gov.au",
        # Annual percentile summary (not per-scene ga_ls_fc_3, which needs an exact
        # acquisition time and 400s for generic polygons). First band = pv_pc_10.
        coverage_id="ga_ls_fc_pc_cyear_3", protocol_version="2.0.1",
        variable=Variable(name="fractional_cover", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Fractional cover percentiles (veg / non-veg / bare soil)"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0",
        citation="DEA, Fractional Cover (Landsat)",
        crs="EPSG:3577", output_format="image/geotiff",
        default_time="2023-01-01T00:00:00Z",
    )


# Disabled: ga_ls_mangrove_cover_cyear_3 is a sparse coastal-only product. DEA WCS
# returns 404 "no data" (not a zero raster) for any polygon without mangroves — i.e.
# almost all generic AU land polygons — so it cannot serve zonal extraction. It only
# succeeds over actual mangrove pixels. Verified 2026-05-30.
# @register("australia_mangrove")
class AustraliaMangroveConnector(NationalWCSConnector):
    slug = "australia_mangrove"
    display_name = "Australia Mangrove Cover (DEA)"
    base_url = "https://ows.dea.ga.gov.au"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_mangrove",
        display_name="Australia Mangrove Canopy Cover 25m (DEA)",
        wcs_url="https://ows.dea.ga.gov.au",
        coverage_id="ga_ls_mangrove_cover_cyear_3", protocol_version="2.0.1",
        variable=Variable(name="mangrove_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Mangrove canopy cover extent (annual, Landsat)"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="DEA, Mangrove Canopy Cover",
        crs="EPSG:3577", output_format="image/geotiff",
        default_time="2023-01-01T00:00:00Z",
    )


# ═══════════════════════════════════════════════════════════════════════
#  ASIA — additional
# ═══════════════════════════════════════════════════════════════════════

@register("india_soil")
class IndiaSoilConnector(NationalWCSConnector):
    slug = "india_soil"
    display_name = "India Soil Map (NBSS&LUP)"
    base_url = "https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="india_soil",
        display_name="India Soil Map (NBSS&LUP / Bhuvan)",
        wcs_url="https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms",
        coverage_id="soil:SOIL_TEX_250K",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Indian soil classification"),
        resolution_m=500, category="soil",
        bbox=BoundingBox(min_lon=68.1, min_lat=6.7, max_lon=97.4, max_lat=37.1),
        license="Open (ISRO/Bhuvan)", citation="NBSS&LUP / ISRO Bhuvan",
        use_wms=True,
    )


@register("india_lulc")
class IndiaLULCConnector(NationalWCSConnector):
    slug = "india_lulc"
    display_name = "India LULC (NRSC)"
    base_url = "https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="india_lulc",
        display_name="India Land Use/Land Cover (NRSC/Bhuvan)",
        wcs_url="https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms",
        coverage_id="sisdp_phase2:lulc_phase2_india",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Indian land use/land cover classification"),
        resolution_m=50, category="land_cover",
        bbox=BoundingBox(min_lon=68.1, min_lat=6.7, max_lon=97.4, max_lat=37.1),
        license="Open (ISRO/Bhuvan)", citation="NRSC/ISRO, LULC 1:50k",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  OCEANIA — additional
# ═══════════════════════════════════════════════════════════════════════

@register("australia_soil_depth")
class AustraliaSoilDepthConnector(NationalWCSConnector):
    slug = "australia_soil_depth"
    display_name = "Australia Soil Depth (SLGA)"
    base_url = "https://www.asris.csiro.au/arcgis/services/TERN"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_soil_depth",
        display_name="Australia Soil Depth (SLGA/TERN)",
        wcs_url="https://www.asris.csiro.au/ArcGIS/services/TERN/DES_ACLEP_AU_TRN_N/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="soil_depth", units="m", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 20),
                          description="Estimated soil depth (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, Soil and Landscape Grid of Australia",
    )


# ═══════════════════════════════════════════════════════════════════════
#  EAST AFRICA
# ═══════════════════════════════════════════════════════════════════════

@register("ethiopia_lc")
class EthiopiaLCConnector(NationalWCSConnector):
    slug = "ethiopia_lc"
    display_name = "Ethiopia Land Cover (ESA CCI)"
    base_url = "https://ows.digitalearth.africa"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ethiopia_lc",
        display_name="Ethiopia ESA CCI Land Cover (via DEA Africa)",
        wcs_url="https://ows.digitalearth.africa/wcs",
        coverage_id="esa_worldcover_2021", protocol_version="2.0.1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Ethiopian land cover (ESA WorldCover via DEA Africa)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=33.0, min_lat=3.4, max_lon=48.0, max_lat=14.9),
        license="CC-BY 4.0",
        citation="ESA WorldCover via Digital Earth Africa",
    )


@register("nigeria_lc")
class NigeriaLCConnector(NationalWCSConnector):
    slug = "nigeria_lc"
    display_name = "Nigeria Land Cover (NASRDA)"
    base_url = "https://ows.digitalearth.africa"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="nigeria_lc",
        display_name="Nigeria ESA WorldCover (via DEA Africa)",
        wcs_url="https://ows.digitalearth.africa/wcs",
        coverage_id="esa_worldcover_2021", protocol_version="2.0.1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Nigerian land cover (ESA WorldCover via DEA Africa)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=2.7, min_lat=4.3, max_lon=14.7, max_lat=13.9),
        license="CC-BY 4.0", citation="ESA WorldCover via Digital Earth Africa",
    )


@register("indonesia_lc")
class IndonesiaLCConnector(NationalWCSConnector):
    slug = "indonesia_lc"
    display_name = "Indonesia Land Cover (KLHK)"
    base_url = "https://geoportal.menlhk.go.id/server/rest/services"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="indonesia_lc",
        display_name="Indonesia Land Cover (KLHK/MoEF)",
        wcs_url="https://geoportal.menlhk.go.id/server/services/SIGAP_Interaktif/NSDH_Penutupan_Lahan_2022/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Indonesian land cover classification (23 classes)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=95, min_lat=-11, max_lon=141, max_lat=6),
        license="Open (KLHK)", citation="KLHK, Peta Penutupan Lahan Indonesia",
    )


# ═══════════════════════════════════════════════════════════════════════
#  AUSTRALIA — additional TERN/SLGA layers
# ═══════════════════════════════════════════════════════════════════════

@register("australia_clay")
class AustraliaClayConnector(NationalWCSConnector):
    slug = "australia_clay"
    display_name = "Australia Clay Content (SLGA)"
    base_url = "https://www.asris.csiro.au/arcgis/services/TERN"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_clay",
        display_name="Australia Clay Content 0-30cm (SLGA/TERN)",
        wcs_url="https://www.asris.csiro.au/ArcGIS/services/TERN/CLY_ACLEP_AU_TRN_N/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="clay", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Clay content in 0-30cm (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, SLGA Clay Content",
    )


@register("australia_sand")
class AustraliaSandConnector(NationalWCSConnector):
    slug = "australia_sand"
    display_name = "Australia Sand Content (SLGA)"
    base_url = "https://www.asris.csiro.au/arcgis/services/TERN"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_sand",
        display_name="Australia Sand Content 0-30cm (SLGA/TERN)",
        wcs_url="https://www.asris.csiro.au/ArcGIS/services/TERN/SND_ACLEP_AU_TRN_N/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="sand", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Sand content in 0-30cm (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, SLGA Sand Content",
    )


@register("australia_ph")
class AustraliaPHConnector(NationalWCSConnector):
    slug = "australia_ph"
    display_name = "Australia Soil pH (SLGA)"
    base_url = "https://www.asris.csiro.au/arcgis/services/TERN"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_ph",
        display_name="Australia Soil pH 0-30cm (SLGA/TERN)",
        wcs_url="https://www.asris.csiro.au/ArcGIS/services/TERN/PHC_ACLEP_AU_TRN_N/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="soil_ph", units="pH", data_type=DataType.CONTINUOUS,
                          valid_range=(2, 12),
                          description="Soil pH (CaCl2) in 0-30cm (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, SLGA Soil pH",
    )


@register("australia_soc")
class AustraliaSOCConnector(NationalWCSConnector):
    slug = "australia_soc"
    display_name = "Australia Organic Carbon (SLGA)"
    base_url = "https://www.asris.csiro.au/arcgis/services/TERN"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_soc",
        display_name="Australia Organic Carbon 0-30cm (SLGA/TERN)",
        wcs_url="https://www.asris.csiro.au/ArcGIS/services/TERN/SOC_ACLEP_AU_TRN_N/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="soc", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 60),
                          description="Soil organic carbon in 0-30cm (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, SLGA Soil Organic Carbon",
    )


@register("australia_awc")
class AustraliaAWCConnector(NationalWCSConnector):
    slug = "australia_awc"
    display_name = "Australia Available Water Capacity (SLGA)"
    base_url = "https://www.asris.csiro.au/arcgis/services/TERN"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="australia_awc",
        display_name="Australia Available Water Capacity (SLGA/TERN)",
        wcs_url="https://www.asris.csiro.au/ArcGIS/services/TERN/AWC_ACLEP_AU_TRN_N/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="awc", units="mm", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 500),
                          description="Available water capacity 0-200cm (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, SLGA Available Water Capacity",
    )


