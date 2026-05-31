# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""National WCS/WMS connectors — Americas (US, Canada, Brazil, Argentina, Mexico, Chile, Peru, Colombia)."""

from __future__ import annotations

from cas.connectors.national_wcs import NationalDatasetConfig, NationalWCSConnector
from cas.core.models import BoundingBox, DataType, Variable
from cas.core.registry import register

ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 9000))


# ═══════════════════════════════════════════════════════════════════════
#  AMERICAS
# ═══════════════════════════════════════════════════════════════════════

@register("canada_hrdem")
class CanadaHRDEMConnector(NationalWCSConnector):
    slug = "canada_hrdem"
    display_name = "Canada HRDEM 1-2m"
    base_url = "https://datacube.services.geo.ca/ows/elevation"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="canada_hrdem", display_name="Canada HRDEM 1-2m (NRCan)",
        wcs_url="https://datacube.services.geo.ca/ows/elevation",
        coverage_id="dsm", variable=ELEV_VAR, resolution_m=2,
        bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
        license="Open Government Licence - Canada", citation="NRCan, High Resolution DEM",
        # Server's WMS 1.1.1 handler crashes (ignores SRS=); only 1.3.0 (CRS=) works.
        use_wms=True, wms_version="1.3.0",
    )


# ═══════════════════════════════════════════════════════════════════════
#  AMERICAS — SOIL & LAND COVER
# ═══════════════════════════════════════════════════════════════════════

@register("brazil_soil")
class BrazilSoilConnector(NationalWCSConnector):
    slug = "brazil_soil"
    display_name = "Brazil Soil Map (EMBRAPA)"
    base_url = "https://geoinfo.dados.embrapa.br/geoserver/geonode/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="brazil_soil", display_name="Brazil Soil Map (EMBRAPA GeoInfo)",
        wcs_url="https://geoinfo.dados.embrapa.br/geoserver/geonode/wms",
        coverage_id="geonode:soils_brazil_wrb_wgs84",
        use_wms=True,
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Brazilian soil classification (WRB)"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=-74, min_lat=-34, max_lon=-34, max_lat=6),
        license="CC-BY 4.0", citation="EMBRAPA, Mapa de Solos do Brasil",
    )


@register("canada_aafc_crops")
class CanadaAAFCCropsConnector(NationalWCSConnector):
    slug = "canada_aafc_crops"
    display_name = "Canada Annual Crop Inventory"
    base_url = "https://agriculture.canada.ca/imagery-images/services/annual_crop_inventory/2024/ImageServer/WCSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="canada_aafc_crops", display_name="Canada AAFC Annual Crop Inventory 30m",
        wcs_url="https://agriculture.canada.ca/imagery-images/services/annual_crop_inventory/2024/ImageServer/WCSServer",
        coverage_id="annual_crop_inventory_2024",
        variable=Variable(name="cropland", units="class", data_type=DataType.CATEGORICAL,
                          description="Canadian crop type classification (~70 classes, annual)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
        license="Open Government Licence - Canada", citation="AAFC, Annual Crop Inventory",
        use_wms=True,
    )


@register("argentina_soil")
class ArgentinaSoilConnector(NationalWCSConnector):
    slug = "argentina_soil"
    display_name = "Argentina Soil Map (INTA)"
    base_url = "https://geo-backend.inta.gob.ar/geoserver/ows"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="argentina_soil", display_name="Argentina Soil Map 1:500k (INTA GeoINTA)",
        wcs_url="https://geo-backend.inta.gob.ar/geoserver/ows",
        coverage_id="geonode:suelos_argentina_1_500",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Argentine soil classification 1:500,000"),
        resolution_m=500, category="soil",
        bbox=BoundingBox(min_lon=-74, min_lat=-56, max_lon=-53, max_lat=-21),
        license="Open (INTA)", citation="INTA, GeoINTA Suelos",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  MEXICO
# ═══════════════════════════════════════════════════════════════════════

@register("mexico_lc")
class MexicoLCConnector(NationalWCSConnector):
    slug = "mexico_lc"
    display_name = "Mexico Land Cover (INEGI)"
    base_url = "https://gaia.inegi.org.mx/NLB/tunnel/wms/wms61"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="mexico_lc", display_name="Mexico Uso de Suelo y Vegetación (INEGI)",
        wcs_url="https://gaia.inegi.org.mx/NLB/tunnel/wms/wms61",
        coverage_id="Hipsografico",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Mexican land use and vegetation classification"),
        resolution_m=250, category="land_cover",
        bbox=BoundingBox(min_lon=-118, min_lat=14, max_lon=-86, max_lat=33),
        license="Open (INEGI)", citation="INEGI, Uso de Suelo y Vegetación",
        use_wms=True,
    )


@register("mexico_soil")
class MexicoSoilConnector(NationalWCSConnector):
    slug = "mexico_soil"
    display_name = "Mexico Soil Map (INEGI)"
    base_url = "https://gaia.inegi.org.mx/NLB/tunnel/wms/wms61"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="mexico_soil", display_name="Mexico Edafología (INEGI)",
        wcs_url="https://gaia.inegi.org.mx/NLB/tunnel/wms/wms61",
        coverage_id="Servicio_WMS_MDM61",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Mexican soil classification (WRB)"),
        resolution_m=250, category="soil",
        bbox=BoundingBox(min_lon=-118, min_lat=14, max_lon=-86, max_lat=33),
        license="Open (INEGI)", citation="INEGI, Carta Edafológica",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  COLOMBIA
# ═══════════════════════════════════════════════════════════════════════

@register("colombia_lc")
class ColombiaLCConnector(NationalWCSConnector):
    slug = "colombia_lc"
    display_name = "Colombia Land Cover (IDEAM)"
    base_url = "https://visualizador.ideam.gov.co/gisserver/services/Estado_Cobertura_Tierra/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="colombia_lc",
        display_name="Colombia CORINE Land Cover (IDEAM)",
        wcs_url="https://visualizador.ideam.gov.co/gisserver/services/Estado_Cobertura_Tierra/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Colombian CORINE land cover adaptation"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=-82, min_lat=-5, max_lon=-67, max_lat=13),
        license="Open (IDEAM)", citation="IDEAM, Coberturas de la Tierra",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  CHILE — soil
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  PERU — land cover
# ═══════════════════════════════════════════════════════════════════════

@register("peru_lc")
class PeruLCConnector(NationalWCSConnector):
    slug = "peru_lc"
    display_name = "Peru Land Cover (MINAM)"
    base_url = "https://geoservidorperu.minam.gob.pe/arcgis/services/ServicioTematico/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="peru_lc",
        display_name="Peru National Land Cover (MINAM)",
        wcs_url="https://geoservidorperu.minam.gob.pe/arcgis/services/ServicioTematico/MapServer/WMSServer",
        coverage_id="1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Peruvian national vegetation/land cover classification (60 classes)"),
        resolution_m=250, category="land_cover",
        bbox=BoundingBox(min_lon=-82, min_lat=-19, max_lon=-68, max_lat=1),
        license="Open (MINAM)", citation="MINAM, Mapa Nacional de Cobertura Vegetal",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  MEXICO — additional (CONABIO land use)
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  ARGENTINA — land cover (MapBiomas via WMS)
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  US — USGS WBD Watershed Boundaries
# ═══════════════════════════════════════════════════════════════════════

@register("usgs_wbd")
class USGSWBDConnector(NationalWCSConnector):
    slug = "usgs_wbd"
    display_name = "USGS Watershed Boundaries (WBD)"
    base_url = "https://hydro.nationalmap.gov/arcgis/services/wbd/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="usgs_wbd",
        display_name="USGS Watershed Boundary Dataset (HUC)",
        wcs_url="https://hydro.nationalmap.gov/arcgis/services/wbd/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="watershed", units="class", data_type=DataType.CATEGORICAL,
                          description="US hydrologic unit code (HUC) watershed boundaries"),
        resolution_m=30, category="hydrology",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, Watershed Boundary Dataset",
        use_wms=True,
    )


@register("usgs_nhd")
class USGSNHDConnector(NationalWCSConnector):
    slug = "usgs_nhd"
    display_name = "USGS National Hydrography (NHD)"
    base_url = "https://hydro.nationalmap.gov/arcgis/services/nhd/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="usgs_nhd",
        display_name="USGS National Hydrography Dataset (NHD)",
        wcs_url="https://hydro.nationalmap.gov/arcgis/services/nhd/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="hydrography", units="class", data_type=DataType.CATEGORICAL,
                          description="US rivers, lakes, streams, flowlines, and water bodies"),
        resolution_m=10, category="hydrology",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, National Hydrography Dataset",
        use_wms=True,
    )


# Disabled: mrdata.usgs.gov/services/sgmc is a *styled* WMS only (no WCS — a
# WCS GetCapabilities 400s). Every GetMap format returns a colorized PNG of the
# scanned 1:500k map, so the connector would read the red channel and report RGB
# byte values as "geology classes" — meaningless categorical data, not SGMC
# lithology codes. There is no class-coded raster/legend endpoint to key against.
# Verified 2026-05-30.
# Re-enable path: switch to a classified SGMC source (e.g. the SGMC geodatabase
# rasterized to unit codes, or a feature service with a Generalized_Lith field)
# and add a code->name legend, then restore @register.
# @register("usgs_geology")
class USGSGeologyConnector(NationalWCSConnector):
    slug = "usgs_geology"
    display_name = "USGS State Geologic Map (US)"
    base_url = "https://mrdata.usgs.gov/services/sgmc"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="usgs_geology",
        display_name="USGS State Geologic Map Compilation (SGMC)",
        wcs_url="https://mrdata.usgs.gov/services/sgmc",
        coverage_id="sgmc2",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="US state geologic map compilation — lithology and age"),
        resolution_m=250, category="geology",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, State Geologic Map Compilation (SGMC)",
        use_wms=True,
    )


@register("usgs_karst")
class USGSKarstConnector(NationalWCSConnector):
    slug = "usgs_karst"
    display_name = "USGS Karst Map (US)"
    base_url = "https://mrdata.usgs.gov/services/kb"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="usgs_karst",
        display_name="USGS Karst/Pseudokarst Map (US)",
        wcs_url="https://mrdata.usgs.gov/services/kb",
        coverage_id="KB_Geology",
        variable=Variable(name="karst", units="class", data_type=DataType.CATEGORICAL,
                          description="Karst and pseudokarst areas — controls subsurface drainage"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, Karst Map of the US",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  US — NLCD Land Cover Change Index
# ═══════════════════════════════════════════════════════════════════════

@register("nlcd_change_index")
class NLCDChangeIndexConnector(NationalWCSConnector):
    slug = "nlcd_change_index"
    display_name = "NLCD Land Cover Change Index (US)"
    base_url = "https://www.mrlc.gov/geoserver/mrlc_display/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="nlcd_change_index",
        display_name="NLCD Land Cover Change Index 2001-2021 30m (MRLC)",
        wcs_url="https://www.mrlc.gov/geoserver/mrlc_download/wcs",
        coverage_id="mrlc_download:NLCD_2001_2021_Land_Cover_Change_Index_L48",
        variable=Variable(name="lc_change_index", units="class", data_type=DataType.CATEGORICAL,
                          description="NLCD land cover change index 2001-2021 — identifies changed areas"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-130, min_lat=22, max_lon=-64, max_lat=52),
        license="Public Domain",
        citation="USGS MRLC, NLCD Land Cover Change Index 2001-2021",
    )


# ═══════════════════════════════════════════════════════════════════════
#  CANADA — FGP land cover
# ═══════════════════════════════════════════════════════════════════════

@register("canada_fgp_lc")
class CanadaFGPLCConnector(NationalWCSConnector):
    slug = "canada_fgp_lc"
    display_name = "Canada Land Cover 2015 (NRCan FGP)"
    base_url = "https://geoappext.nrcan.gc.ca/arcgis/services/FGP/canada_landcover_2015_en/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="canada_fgp_lc",
        display_name="Canada Land Cover 2015 30m (NRCan FGP)",
        wcs_url="https://geoappext.nrcan.gc.ca/arcgis/services/FGP/canada_landcover_2015_en/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Canadian national land cover 2015 (FGP)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
        license="Open Government Licence - Canada",
        citation="NRCan, Canada Land Cover 2015",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  SOUTH AMERICA
# ═══════════════════════════════════════════════════════════════════════

@register("brazil_biomes")
class BrazilBiomesConnector(NationalWCSConnector):
    slug = "brazil_biomes"
    display_name = "Brazil Biomes (IBGE)"
    base_url = "https://geoservicos.ibge.gov.br/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="brazil_biomes",
        display_name="Brazil Biomes 1:5M (IBGE)",
        wcs_url="https://geoservicos.ibge.gov.br/geoserver/wms",
        coverage_id="BDIA:gpc_geol",
        variable=Variable(name="biome", units="class", data_type=DataType.CATEGORICAL,
                          description="Brazilian biomes (Amazon, Cerrado, Caatinga, etc.)"),
        resolution_m=250, category="land_cover",
        bbox=BoundingBox(min_lon=-74, min_lat=-34, max_lon=-34, max_lat=6),
        license="Open (IBGE)", citation="IBGE, Mapa de Biomas do Brasil",
        use_wms=True,
    )


# Disabled: geoservicios.midagri.gob.pe refuses TCP connections from the CI/US
# vantage (no response on 80/443) — host down or geo-restricted. Verified 2026-05-30.
# @register("peru_soil")
class PeruSoilConnector(NationalWCSConnector):
    slug = "peru_soil"
    display_name = "Peru Soil Map (MIDAGRI)"
    base_url = "https://geoservicios.midagri.gob.pe/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="peru_soil",
        display_name="Peru Soil Capacity Map (MIDAGRI)",
        wcs_url="https://geoservicios.midagri.gob.pe/geoserver/wms",
        coverage_id="midagri:capacidad_uso_suelo",
        variable=Variable(name="soil_capacity", units="class", data_type=DataType.CATEGORICAL,
                          description="Peruvian soil use capacity classification"),
        resolution_m=250, category="soil",
        bbox=BoundingBox(min_lon=-81.3, min_lat=-18.4, max_lon=-68.7, max_lat=-0.04),
        license="Open (MIDAGRI)", citation="MIDAGRI, Capacidad de Uso Mayor de Suelos",
    )


# ═══════════════════════════════════════════════════════════════════════
#  US — additional datasets
# ═══════════════════════════════════════════════════════════════════════

# Disabled: the CDLService endpoint is an Axis2 SOAP/REST service, not a WCS
# (GetCapabilities → SOAP fault "Operation not found"). Duplicates usda_cdl.
# @register("us_nass_cropscape")
class USCropscapeConnector(NationalWCSConnector):
    slug = "us_nass_cropscape"
    display_name = "US CropScape 30m (NASS)"
    base_url = "https://nassgeodata.gmu.edu/CropScape"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="us_nass_cropscape",
        display_name="US Cropland Data Layer 30m (USDA NASS CropScape)",
        wcs_url="https://nassgeodata.gmu.edu/axis2/services/CDLService/wcs",
        coverage_id="CDL_2023",
        variable=Variable(name="crop_type", units="class", data_type=DataType.CATEGORICAL,
                          description="US crop type classification (~130 classes, annual)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-127, min_lat=24, max_lon=-65, max_lat=50),
        license="Public Domain", citation="USDA NASS, Cropland Data Layer 2023",
    )


@register("us_ned_slope")
class USNEDSlopeConnector(NationalWCSConnector):
    slug = "us_ned_slope"
    display_name = "US Slope 10m (3DEP)"
    base_url = "https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="us_ned_slope",
        display_name="US Slope 10m (USGS 3DEP derived)",
        wcs_url="https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer",
        coverage_id="3DEPElevation",
        variable=Variable(name="slope", units="degrees", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 90),
                          description="Slope from USGS 3DEP NED 10m"),
        resolution_m=10, category="terrain",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, 3DEP Elevation Products",
    )


# Disabled: the entire sciencebase.gov /arcgis/ ArcGIS Server is offline (every
# REST/WCS path hangs; site root responds). Backend decommissioned. Verified 2026-05-30.
# @register("us_gap_lc")
class USGAPLCConnector(NationalWCSConnector):
    slug = "us_gap_lc"
    display_name = "US GAP Land Cover 30m"
    base_url = "https://www.sciencebase.gov/arcgis/services"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="us_gap_lc",
        display_name="US GAP/LANDFIRE National Land Cover 30m",
        wcs_url="https://www.sciencebase.gov/arcgis/services/GAP/GAP_Land_Cover_NVC_Class_Landuse/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="US ecological land cover (NatureServe NVC classes)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS GAP/LANDFIRE, National Land Cover",
    )


# ═══════════════════════════════════════════════════════════════════════
#  CANADA — additional datasets
# ═══════════════════════════════════════════════════════════════════════

@register("canada_geology")
class CanadaGeologyConnector(NationalWCSConnector):
    slug = "canada_geology"
    display_name = "Canada Bedrock Geology (GSC)"
    base_url = "https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="canada_geology",
        display_name="Canada Bedrock Geology (GSC/NRCan)",
        wcs_url="https://maps-cartes.services.geo.ca/server_serveur/services/NRCan/gsc_bedrock_geology_en/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Canadian bedrock geology 1:5M"),
        resolution_m=5000, category="geology",
        bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
        license="Open Government Licence - Canada",
        citation="GSC/NRCan, Canadian Geoscience Map",
        use_wms=True,
    )


