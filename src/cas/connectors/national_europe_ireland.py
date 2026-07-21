# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Irish national WMS connectors extracted from :mod:`national_europe`."""

from __future__ import annotations

from cas.connectors.national_wcs import NationalDatasetConfig, NationalWCSConnector
from cas.core.models import BoundingBox, DataType, Variable
from cas.core.registry import register

# ═══════════════════════════════════════════════════════════════════════
#  IRELAND — soil wet/dry classification (EPA)
# ═══════════════════════════════════════════════════════════════════════

@register("ireland_soil_wetdry")
class IrelandSoilWetDryConnector(NationalWCSConnector):
    slug = "ireland_soil_wetdry"
    display_name = "Ireland Soil Wet/Dry (EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_soil_wetdry",
        display_name="Ireland Soil Drainage Classification (EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="SOILS_WETDRY",
        variable=Variable(name="soil_drainage", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish soil wet/dry classification — controls infiltration"),
        resolution_m=250, category="soil",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="EPA / Teagasc, Soil Drainage Classification",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  IRELAND — more EPA hydrology
# ═══════════════════════════════════════════════════════════════════════

@register("ireland_aquifer")
class IrelandAquiferConnector(NationalWCSConnector):
    slug = "ireland_aquifer"
    display_name = "Ireland Aquifer Map (GSI/EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_aquifer",
        display_name="Ireland Bedrock Aquifer Classification (GSI/EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="GEOL_GSI_Aquifer",
        variable=Variable(name="aquifer", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish bedrock aquifer classification (GSI)"),
        resolution_m=250, category="geology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="GSI / EPA, Bedrock Aquifer Map of Ireland",
        use_wms=True,
    )


@register("ireland_groundwater")
class IrelandGroundwaterConnector(NationalWCSConnector):
    slug = "ireland_groundwater"
    display_name = "Ireland Groundwater Bodies (EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_groundwater",
        display_name="Ireland WFD Groundwater Bodies (EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="WFD_GROUNDWATERBODIESActive",
        variable=Variable(name="groundwater_body", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish WFD groundwater body delineation"),
        resolution_m=250, category="hydrology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="EPA, WFD Groundwater Bodies",
        use_wms=True,
    )


@register("ireland_catchments")
class IrelandCatchmentsConnector(NationalWCSConnector):
    slug = "ireland_catchments"
    display_name = "Ireland Catchments (EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_catchments",
        display_name="Ireland Hydrological Catchments (EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="HYDRO_Catchments",
        variable=Variable(name="catchment", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish hydrological catchment delineation"),
        resolution_m=100, category="hydrology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="EPA, Irish Hydrological Catchments",
        use_wms=True,
    )


@register("ireland_flood")
class IrelandFloodConnector(NationalWCSConnector):
    slug = "ireland_flood"
    display_name = "Ireland Flood Extents (EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_flood",
        display_name="Ireland Coastal Flood Extents (EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="CWB_FLOODEXTENTS_HIGH",
        variable=Variable(name="flood_extent", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish coastal flood extent (high probability scenario)"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="EPA, Coastal Flood Extents",
        use_wms=True,
    )


@register("ireland_gravel_aquifer")
class IrelandGravelAquiferConnector(NationalWCSConnector):
    slug = "ireland_gravel_aquifer"
    display_name = "Ireland Sand & Gravel Aquifer (GSI/EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_gravel_aquifer",
        display_name="Ireland Sand & Gravel Aquifer Map (GSI/EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="GEOL_GSI_Aquifer_SandGravel",
        variable=Variable(name="gravel_aquifer", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish sand/gravel aquifer classification"),
        resolution_m=250, category="geology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0",
        citation="GSI / EPA, Sand & Gravel Aquifer Map of Ireland",
        use_wms=True,
    )


@register("ireland_peatland")
class IrelandPeatlandConnector(NationalWCSConnector):
    slug = "ireland_peatland"
    display_name = "Ireland Peatland/Bog Boundaries (BNM)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_peatland",
        display_name="Ireland Bord na Móna Peatland Boundaries",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="BORDNAMONA_BOGBOUNDARIES",
        variable=Variable(name="peatland", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish peatland/bog boundaries — controls carbon/water storage"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="Bord na Móna / EPA, Bog Boundaries",
        use_wms=True,
    )


@register("ireland_gw_vulnerability")
class IrelandGWVulnerabilityConnector(NationalWCSConnector):
    slug = "ireland_gw_vulnerability"
    display_name = "Ireland GW Vulnerability (GSI/EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_gw_vulnerability",
        display_name="Ireland Groundwater Vulnerability (GSI/EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="GEOL_GSI_Vulnerability",
        variable=Variable(name="gw_vulnerability", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish groundwater vulnerability (extreme/high/moderate/low)"),
        resolution_m=250, category="geology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0",
        citation="GSI / EPA, Groundwater Vulnerability Map of Ireland",
        use_wms=True,
    )


@register("ireland_subsoil")
class IrelandSubsoilConnector(NationalWCSConnector):
    slug = "ireland_subsoil"
    display_name = "Ireland Subsoil Map (GSI/EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_subsoil",
        display_name="Ireland Subsoil Classification (GSI/EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="Soil_subsoils_ie",
        variable=Variable(name="subsoil", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish subsoil type (till, gravel, alluvium, peat, bedrock)"),
        resolution_m=250, category="soil",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="GSI / EPA, Subsoil Map of Ireland",
        use_wms=True,
    )


@register("ireland_bedrock")
class IrelandBedrockConnector(NationalWCSConnector):
    slug = "ireland_bedrock"
    display_name = "Ireland Bedrock Geology (GSI/EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_bedrock",
        display_name="Ireland Bedrock Geology 1:1M (GSI/EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="GEOL_GSI_Bedrock1Million",
        variable=Variable(name="bedrock_geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish bedrock geological classification"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="GSI / EPA, Bedrock Geology Map of Ireland",
        use_wms=True,
    )


