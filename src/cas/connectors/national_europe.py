# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""National WCS/WMS connectors — Europe (Nordic, Western, Central, Southern, UK/Ireland)."""

from __future__ import annotations

from cas.connectors.national_wcs import NationalDatasetConfig, NationalWCSConnector
from cas.core.models import BoundingBox, DataType, Variable
from cas.core.registry import register

ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 9000))


@register("norway_dem")
class NorwayDEMConnector(NationalWCSConnector):
    slug = "norway_dem"
    display_name = "Norway DTM 1m"
    base_url = "https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_dem", display_name="Norway DTM 1m (Kartverket)",
        wcs_url="https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833",
        coverage_id="nhm_dtm_topo_25833", variable=ELEV_VAR,
        resolution_m=1, crs="EPSG:25833",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="CC-0 (Norway Open Data)", citation="Kartverket, National Detailed Height Model",
        use_wms=True,
    )


@register("finland_dem")
class FinlandDEMConnector(NationalWCSConnector):
    slug = "finland_dem"
    display_name = "Finland DEM 2m"
    base_url = "https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="finland_dem", display_name="Finland Elevation Model 2m (MML)",
        wcs_url="https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2",
        coverage_id="korkeusmalli_2m", variable=ELEV_VAR,
        resolution_m=2, protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="Maanmittauslaitos, Elevation Model 2m",
        auth_token_env="CAS_MML_API_KEY",
    )


@register("denmark_dem")
class DenmarkDEMConnector(NationalWCSConnector):
    slug = "denmark_dem"
    display_name = "Denmark DHM 0.4m"
    base_url = "https://api.dataforsyningen.dk/dhm_wcs_DAF"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_dem", display_name="Denmark DHM 0.4m (SDFI)",
        wcs_url="https://api.dataforsyningen.dk/dhm_wcs_DAF",
        coverage_id="dhm_terraen", variable=ELEV_VAR,
        resolution_m=0.4,
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (Denmark)", citation="SDFI, Danmarks Højdemodel",
        auth_token_env="CAS_DATAFORSYNINGEN_TOKEN",
    )


# @register("estonia_dem")  # Disabled: URL/coverage_id point to aerial photo service, not DEM
class EstoniaDEMConnector(NationalWCSConnector):
    slug = "estonia_dem"
    display_name = "Estonia DEM 1m"
    base_url = "https://kaart.maaamet.ee/wms/fotokaart"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="estonia_dem", display_name="Estonia DEM 1m (Maa-amet)",
        wcs_url="https://kaart.maaamet.ee/wms/fotokaart",
        coverage_id="MA-FOTOKAART", variable=ELEV_VAR,
        resolution_m=1,
        crs="EPSG:3301",
        bbox=BoundingBox(min_lon=21.8, min_lat=57.5, max_lon=28.2, max_lat=59.7),
        license="Estonian Open Data License", citation="Maa-amet, Estonian Land Board",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  WESTERN EUROPE
# ═══════════════════════════════════════════════════════════════════════

@register("uk_lidar")
class UKLiDARConnector(NationalWCSConnector):
    slug = "uk_lidar"
    display_name = "UK LiDAR DTM 1m"
    base_url = "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_lidar", display_name="UK LiDAR DTM 1m (EA England)",
        wcs_url="https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs",
        coverage_id="Lidar_Composite_DTM_1m",
        variable=ELEV_VAR, resolution_m=1, protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="Open Government Licence v3", citation="Environment Agency, LiDAR Composite DTM",
        use_wms=True,
    )


@register("netherlands_ahn")
class NetherlandsAHNConnector(NationalWCSConnector):
    slug = "netherlands_ahn"
    display_name = "Netherlands AHN4 0.5m"
    base_url = "https://service.pdok.nl/rws/ahn/wcs/v1_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_ahn", display_name="Netherlands AHN4 0.5m (PDOK)",
        wcs_url="https://service.pdok.nl/rws/ahn/wcs/v1_0",
        coverage_id="dtm_05m", variable=ELEV_VAR,
        resolution_m=0.5,
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0 (Netherlands)", citation="Rijkswaterstaat, Actueel Hoogtebestand Nederland 4",
    )


# @register("italy_tinitaly")  # Disabled: tinitaly.pi.ingv.it unreachable
class ItalyTINITALYConnector(NationalWCSConnector):
    slug = "italy_tinitaly"
    display_name = "Italy TINITALY 10m"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_tinitaly", display_name="Italy TINITALY/1.1 10m (INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1:tinitaly_dem",
        variable=ELEV_VAR, resolution_m=10,
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1",
    )


@register("spain_mdt")
class SpainMDTConnector(NationalWCSConnector):
    slug = "spain_mdt"
    display_name = "Spain MDT 5m"
    base_url = "https://servicios.idee.es/wcs-inspire/mdt"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_mdt", display_name="Spain MDT05 5m (IGN/CNIG)",
        wcs_url="https://servicios.idee.es/wcs-inspire/mdt",
        coverage_id="Elevacion4258_5",
        variable=ELEV_VAR, resolution_m=5, protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="CC-BY 4.0", citation="IGN/CNIG, Modelo Digital del Terreno",
    )


# Disabled: mapy.geoportal.gov.pl refuses TCP connections from non-PL IPs (CI/US
# vantage) — likely geo-restricted. Re-enable if running from an EU IP. 2026-05-30.
# @register("poland_nmt")
class PolandNMTConnector(NationalWCSConnector):
    slug = "poland_nmt"
    display_name = "Poland NMT 1m"
    base_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModel"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="poland_nmt", display_name="Poland NMT 1m (GUGiK)",
        wcs_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModel",
        coverage_id="DTM_PL-KRON86-NH",
        variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=14.1, min_lat=49.0, max_lon=24.2, max_lat=54.9),
        license="Open (Poland)", citation="GUGiK, Numeryczny Model Terenu",
        crs="EPSG:2180",
        use_wms=True,
    )


@register("slovenia_dem")
class SloveniaDEMConnector(NationalWCSConnector):
    slug = "slovenia_dem"
    display_name = "Slovenia DEM 1m"
    base_url = "https://ipi.eprostor.gov.si/wms-si-gurs-dts/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="slovenia_dem", display_name="Slovenia DMV 1m (GURS)",
        wcs_url="https://ipi.eprostor.gov.si/wms-si-gurs-dts/wms",
        coverage_id="SI.GURS.ZPDZ:PREGLEDNI_DOF", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=13.4, min_lat=45.4, max_lon=16.6, max_lat=46.9),
        license="CC-BY 4.0", citation="GURS, Digitalni Model Višin",
        use_wms=True,
    )


@register("netherlands_soil")
class NetherlandsSoilConnector(NationalWCSConnector):
    slug = "netherlands_soil"
    display_name = "Netherlands Soil Map (BRO)"
    base_url = "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_soil", display_name="Netherlands Bodemkaart 1:50k (BRO/PDOK)",
        wcs_url="https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0",
        coverage_id="soilarea",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Dutch soil classification 1:50,000"),
        resolution_m=50, category="soil",
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="BRO, Bodemkaart van Nederland 1:50.000",
        use_wms=True,
    )


@register("finland_soil")
class FinlandSoilConnector(NationalWCSConnector):
    slug = "finland_soil"
    display_name = "Finland Soil Map (GTK)"
    base_url = "https://gtkdata.gtk.fi/arcgis/services/Rajapinnat/GTK_Maapera_WMS/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="finland_soil", display_name="Finland Soil Map (GTK Maaperäkartta)",
        wcs_url="https://gtkdata.gtk.fi/arcgis/services/Rajapinnat/GTK_Maapera_WMS/MapServer/WMSServer",
        coverage_id="Turvetuotantoalueiden_maanpeite_1.0/202350261",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Finnish quaternary deposits classification"),
        resolution_m=20, category="soil",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="GTK, Geological Survey of Finland",
        use_wms=True,
    )


@register("finland_forest_height")
class FinlandForestHeightConnector(NationalWCSConnector):
    slug = "finland_forest_height"
    display_name = "Finland Forest Height (LUKE)"
    base_url = "https://kartta.luke.fi/geoserver/MVMI/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="finland_forest_height",
        display_name="Finland Mean Forest Height (LUKE MVMI)",
        wcs_url="https://kartta.luke.fi/geoserver/MVMI/wms",
        coverage_id="keskipituus",
        variable=Variable(name="tree_height", units="m", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 35),
                          description="Finnish forest mean tree height — controls interception"),
        resolution_m=16, category="vegetation",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="LUKE, Multi-Source National Forest Inventory (MVMI)",
        use_wms=True,
    )


@register("finland_site_type")
class FinlandSiteTypeConnector(NationalWCSConnector):
    slug = "finland_site_type"
    display_name = "Finland Forest Site Type (LUKE)"
    base_url = "https://kartta.luke.fi/geoserver/MVMI/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="finland_site_type",
        display_name="Finland Forest Site Fertility (LUKE MVMI)",
        wcs_url="https://kartta.luke.fi/geoserver/MVMI/wms",
        coverage_id="kasvupaikka_1923",
        variable=Variable(name="site_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Finnish forest site type (fertility class)"),
        resolution_m=16, category="vegetation",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="LUKE, Multi-Source National Forest Inventory (MVMI)",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NATIONAL LAND COVER (WMS)
# ═══════════════════════════════════════════════════════════════════════

@register("norway_ar5")
class NorwayAR5Connector(NationalWCSConnector):
    slug = "norway_ar5"
    display_name = "Norway AR5 Land Cover"
    base_url = "https://wms.nibio.no/cgi-bin/ar5"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_ar5", display_name="Norway AR5 Land Cover 1:5k (NIBIO)",
        wcs_url="https://wms.nibio.no/cgi-bin/ar5",
        coverage_id="AR5",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian land resource classification (104 classes)"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="CC-BY 4.0 (NLOD)", citation="NIBIO, AR5 Arealressurskart",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMANY
# ═══════════════════════════════════════════════════════════════════════

@register("germany_dgm200")
class GermanyDGM200Connector(NationalWCSConnector):
    slug = "germany_dgm200"
    display_name = "Germany DGM200 200m"
    base_url = "https://sgx.geodatenzentrum.de/wcs_dgm200_inspire"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_dgm200", display_name="Germany DGM200 200m (BKG)",
        wcs_url="https://sgx.geodatenzentrum.de/wcs_dgm200_inspire",
        coverage_id="dgm200_inspire__EL.GridCoverage", variable=ELEV_VAR, resolution_m=200,
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="GeoNutzV (GeoBasis-DE / BKG)", citation="BKG, Digitales Geländemodell 200",
        auth_token_env="CAS_BKG_UUID",
        use_wms=True,
    )


@register("germany_nrw_dgm1")
class GermanyNRWDGM1Connector(NationalWCSConnector):
    slug = "germany_nrw_dgm1"
    display_name = "Germany NRW DGM 1m"
    base_url = "https://www.wcs.nrw.de/geobasis/wcs_nw_dgm"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_nrw_dgm1", display_name="NRW (Germany) DGM 1m LiDAR",
        wcs_url="https://www.wcs.nrw.de/geobasis/wcs_nw_dgm",
        coverage_id="WCS_NW_DGM", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=5.9, min_lat=50.3, max_lon=9.5, max_lat=52.6),
        license="DL-DE/Zero", citation="Geobasis NRW, Digitales Geländemodell 1m",
        crs="EPSG:25832",
        use_wms=True,
    )


@register("germany_soil")
class GermanySoilConnector(NationalWCSConnector):
    slug = "germany_soil"
    display_name = "Germany Soil Map (BGR)"
    base_url = "https://services.bgr.de/wms/boden/buek200/"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_soil", display_name="Germany BÜK200 Soil Map (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/buek200/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="German soil classification 1:200,000"),
        resolution_m=200, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use", citation="BGR, Bodenübersichtskarte 1:200.000",
    )


@register("germany_lc")
class GermanyLCConnector(NationalWCSConnector):
    slug = "germany_lc"
    display_name = "Germany LB-DE Land Cover"
    base_url = "https://sgx.geodatenzentrum.de/wms_lb-de"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_lc", display_name="Germany LB-DE Land Cover (BKG)",
        wcs_url="https://sgx.geodatenzentrum.de/wms_lb-de",
        coverage_id="2023_lb_de",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="German land cover model (CORINE nomenclature, 1ha MMU)"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="GeoNutzV", citation="BKG, Landbedeckungsmodell Deutschland 2021",
        use_wms=True,
    )


@register("germany_field_capacity")
class GermanyFieldCapacityConnector(NationalWCSConnector):
    slug = "germany_field_capacity"
    display_name = "Germany Field Capacity (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/nfkwe1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_field_capacity",
        display_name="Germany Available Water Capacity in Rootzone (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/nfkwe1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="field_capacity", units="mm", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 300),
                          description="Available field capacity in root zone — controls plant water supply"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Nutzbare Feldkapazität im effektiven Wurzelraum",
    )


@register("germany_gw_recharge")
class GermanyGWRechargeConnector(NationalWCSConnector):
    slug = "germany_gw_recharge"
    display_name = "Germany GW Recharge (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/gws1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_gw_recharge",
        display_name="Germany Groundwater Recharge (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/gws1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="gw_recharge", units="mm/yr", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 500),
                          description="Mean annual groundwater recharge from precipitation"),
        resolution_m=1000, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Grundwasserneubildung (GWN)",
    )


@register("germany_soil_water")
class GermanySoilWaterConnector(NationalWCSConnector):
    slug = "germany_soil_water"
    display_name = "Germany Soil Water Balance (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/bodenwasserhaushalt/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_soil_water",
        display_name="Germany Soil Water Balance (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/bodenwasserhaushalt/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_water_balance", units="mm", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 300),
                          description="Soil water holding capacity and effective root depth"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Bodenwasserhaushalt",
    )


@register("germany_humus")
class GermanyHumusConnector(NationalWCSConnector):
    slug = "germany_humus"
    display_name = "Germany Humus Content (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/humus1000ob/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_humus",
        display_name="Germany Topsoil Humus Content (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/humus1000ob/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="humus", units="class", data_type=DataType.CATEGORICAL,
                          description="Topsoil humus content — controls water holding and infiltration"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Humusgehalt der Oberböden",
    )


@register("germany_erosion_water")
class GermanyErosionWaterConnector(NationalWCSConnector):
    slug = "germany_erosion_water"
    display_name = "Germany Water Erosion Risk (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/pegwasser1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_erosion_water",
        display_name="Germany Water Erosion Potential (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/pegwasser1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="erosion_water", units="class", data_type=DataType.CATEGORICAL,
                          description="Potential water erosion risk — controls sediment yield"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Potentielle Erosionsgefährdung durch Wasser",
    )


@register("germany_soil_retention")
class GermanySoilRetentionConnector(NationalWCSConnector):
    slug = "germany_soil_retention"
    display_name = "Germany Soil Water Retention (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/swr1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_soil_retention",
        display_name="Germany Soil Water Retention 1:1M (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/swr1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_water_retention", units="class", data_type=DataType.CATEGORICAL,
                          description="Soil water retention capacity classification"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use", citation="BGR, Standortgerechter Wasserrückhalt",
    )


@register("germany_soil_texture")
class GermanySoilTextureConnector(NationalWCSConnector):
    slug = "germany_soil_texture"
    display_name = "Germany Soil Physical Groups (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/physgru1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_soil_texture",
        display_name="Germany Physical Soil Groups 1:1M (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/physgru1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_texture_group", units="class", data_type=DataType.CATEGORICAL,
                          description="Physical soil texture groups — controls hydraulic properties"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use", citation="BGR, Physikalische Bodengruppen",
    )


@register("germany_soil_quality")
class GermanySoilQualityConnector(NationalWCSConnector):
    slug = "germany_soil_quality"
    display_name = "Germany Soil Quality Rating (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/sqr1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_soil_quality",
        display_name="Germany Müncheberger Soil Quality Rating (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/sqr1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_quality", units="index", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Soil quality rating — integrated soil productivity index"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use", citation="BGR, Müncheberger Soil Quality Rating",
    )


# ═══════════════════════════════════════════════════════════════════════
#  SPAIN — clay minerals
# ═══════════════════════════════════════════════════════════════════════

@register("spain_clay_minerals")
class SpainClayMineralsConnector(NationalWCSConnector):
    slug = "spain_clay_minerals"
    display_name = "Spain Clay Minerals 1:1M (IGME)"
    base_url = "https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Arcillas_1M/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_clay_minerals",
        display_name="Spain Clay Mineral Map 1:1M (IGME)",
        wcs_url="https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Arcillas_1M/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="clay_minerals", units="class", data_type=DataType.CATEGORICAL,
                          description="Clay mineral distribution — controls soil swelling and permeability"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="Open (IGME)",
        citation="IGME, Mapa de Rocas y Minerales Industriales - Arcillas",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  IRELAND — CORINE land cover
# ═══════════════════════════════════════════════════════════════════════

@register("ireland_corine")
class IrelandCORINEConnector(NationalWCSConnector):
    slug = "ireland_corine"
    display_name = "Ireland CORINE Land Cover (EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_corine",
        display_name="Ireland CORINE Land Cover 2018 (EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="LAND_CLC00Rev",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish CORINE land cover classification"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="EPA, CORINE Land Cover Ireland",
        use_wms=True,
    )


@register("ireland_lakes")
class IrelandLakesConnector(NationalWCSConnector):
    slug = "ireland_lakes"
    display_name = "Ireland WFD Lakes (EPA)"
    base_url = "https://gis.epa.ie/geoserver/EPA/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_lakes",
        display_name="Ireland WFD Lake Water Bodies (EPA)",
        wcs_url="https://gis.epa.ie/geoserver/EPA/wms",
        coverage_id="WFD_LAKESEGMENT",
        variable=Variable(name="lake_body", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish WFD lake water body delineation"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="EPA, WFD Lake Water Bodies",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMANY — wind erosion + air capacity
# ═══════════════════════════════════════════════════════════════════════

@register("germany_wind_erosion")
class GermanyWindErosionConnector(NationalWCSConnector):
    slug = "germany_wind_erosion"
    display_name = "Germany Wind Erosion Risk (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/pegwind1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_wind_erosion",
        display_name="Germany Potential Wind Erosion Risk (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/pegwind1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="erosion_wind", units="class", data_type=DataType.CATEGORICAL,
                          description="Wind erosion susceptibility — dust/sediment loss risk"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Potentielle Erosionsgefährdung durch Wind",
    )


@register("germany_air_capacity")
class GermanyAirCapacityConnector(NationalWCSConnector):
    slug = "germany_air_capacity"
    display_name = "Germany Air Capacity in Root Zone (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/lkwe1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_air_capacity",
        display_name="Germany Air Capacity in Root Zone (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/lkwe1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="air_capacity", units="vol%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 50),
                          description="Soil air capacity in root zone — controls aeration and drainage"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Luftkapazität im effektiven Wurzelraum",
    )


@register("germany_cropland_oc")
class GermanyCroplandOCConnector(NationalWCSConnector):
    slug = "germany_cropland_oc"
    display_name = "Germany Cropland Organic Carbon (BGR)"
    base_url = "https://services.bgr.de/arcgis/rest/services/boden/oaacker1000/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_cropland_oc",
        display_name="Germany Cropland Organic Carbon Content (BGR)",
        wcs_url="https://services.bgr.de/arcgis/rest/services/boden/oaacker1000/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="organic_carbon", units="class", data_type=DataType.CATEGORICAL,
                          description="Organic carbon in cropland topsoil — controls water retention"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="BGR Terms of Use",
        citation="BGR, Organische Substanz in Ackerböden",
    )


# ═══════════════════════════════════════════════════════════════════════
#  FRANCE
# ═══════════════════════════════════════════════════════════════════════

@register("france_soil")
class FranceSoilConnector(NationalWCSConnector):
    slug = "france_soil"
    display_name = "France Soil Map (INRAE)"
    base_url = "https://geodata.inrae.fr/geoserver/gissol_rmqs/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="france_soil", display_name="France Soil (GIS Sol / INRAE RMQS)",
        wcs_url="https://geodata.inrae.fr/geoserver/gissol_rmqs/wms",
        coverage_id="gissol_rmqs:rmqs_teneur_pedo",
        variable=Variable(name="soil_properties", units="various", data_type=DataType.CONTINUOUS,
                          description="French soil monitoring network (pH, SOC, trace elements)"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
        license="Open (INRAE)", citation="INRAE / GIS Sol, RMQS",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  SWEDEN
# ═══════════════════════════════════════════════════════════════════════

@register("sweden_lc")
class SwedenLCConnector(NationalWCSConnector):
    slug = "sweden_lc"
    display_name = "Sweden NMD 10m Land Cover"
    base_url = "https://geodata.naturvardsverket.se/inspire/lc-nmd/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="sweden_lc", display_name="Sweden NMD 10m Land Cover (EPA)",
        wcs_url="https://geodata.naturvardsverket.se/inspire/lc-nmd/wms",
        coverage_id="LC.LandCoverRaster.Bas.2018",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Swedish national land cover (10m, Sentinel-2 based)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=11.0, min_lat=55.3, max_lon=24.2, max_lat=69.1),
        license="Open (Sweden)", citation="Naturvårdsverket, Nationella Marktäckedata",
        use_wms=True,
    )


@register("sweden_soil")
class SwedenSoilConnector(NationalWCSConnector):
    slug = "sweden_soil"
    display_name = "Sweden Soil Map (SGU)"
    base_url = "https://maps3.sgu.se/geoserver/jord/ows"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="sweden_soil", display_name="Sweden Quaternary Deposits (SGU)",
        wcs_url="https://maps3.sgu.se/geoserver/jord/ows",
        coverage_id="jord:SE.GOV.SGU.JORD.YTLAGER_JY1.25K",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Swedish quaternary deposits (1:25k-1:100k)"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=11.0, min_lat=55.3, max_lon=24.2, max_lat=69.1),
        license="Open (SGU)", citation="SGU, Geological Survey of Sweden",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  BELGIUM
# ═══════════════════════════════════════════════════════════════════════

@register("belgium_flanders_dem")
class BelgiumFlandersDEMConnector(NationalWCSConnector):
    slug = "belgium_flanders_dem"
    display_name = "Belgium Flanders DHMV 1m"
    base_url = "https://geo.api.vlaanderen.be/DHMV/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_flanders_dem", display_name="Flanders (Belgium) DHMV II DTM 1m",
        wcs_url="https://geo.api.vlaanderen.be/DHMV/wms",
        coverage_id="DHMV-II", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Gratis Open Data Vlaanderen",
        citation="Digitaal Hoogtemodel Vlaanderen II",
        use_wms=True,
    )


@register("belgium_wallonia_dem")
class BelgiumWalloniaDEMConnector(NationalWCSConnector):
    slug = "belgium_wallonia_dem"
    display_name = "Belgium Wallonia MNT 1m"
    base_url = "https://geoservices.wallonie.be/arcgis/services/RELIEF/WALLONIE_MNT_2021_2022/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_wallonia_dem",
        display_name="Wallonia (Belgium) MNT 1m LiDAR 2021-2022",
        wcs_url="https://geoservices.wallonie.be/arcgis/services/RELIEF/WALLONIE_MNT_2021_2022/MapServer/WMSServer",
        coverage_id="0", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=2.8, min_lat=49.5, max_lon=6.4, max_lat=50.8),
        license="Open (Wallonia)", citation="SPW, Modèle Numérique de Terrain 2021-2022",
        use_wms=True,
    )


@register("belgium_soil")
class BelgiumSoilConnector(NationalWCSConnector):
    slug = "belgium_soil"
    display_name = "Belgium Flanders Soil (DOV)"
    base_url = "https://www.dov.vlaanderen.be/geoserver/bodemkaart/bodemtypes/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_soil", display_name="Flanders (Belgium) Soil Map 1:20k (DOV)",
        wcs_url="https://www.dov.vlaanderen.be/geoserver/bodemkaart/bodemtypes/wms",
        coverage_id="bodemtypes",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Belgian soil classification 1:20,000"),
        resolution_m=20, category="soil",
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Open (DOV)", citation="DOV, Bodemkaart van België",
        use_wms=True,
    )


@register("belgium_groundwater")
class BelgiumGroundwaterConnector(NationalWCSConnector):
    slug = "belgium_groundwater"
    display_name = "Belgium Flanders Groundwater (DOV)"
    base_url = "https://www.dov.vlaanderen.be/geoserver/grondwater/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_groundwater",
        display_name="Flanders (Belgium) Groundwater Protection Zones (DOV)",
        wcs_url="https://www.dov.vlaanderen.be/geoserver/grondwater/wms",
        coverage_id="beschermingszones_2014",
        variable=Variable(name="gw_protection", units="class", data_type=DataType.CATEGORICAL,
                          description="Flanders groundwater protection zones"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Open (DOV)",
        citation="DOV, Beschermingszones Grondwater Vlaanderen",
        use_wms=True,
    )


@register("belgium_aquifer")
class BelgiumAquiferConnector(NationalWCSConnector):
    slug = "belgium_aquifer"
    display_name = "Belgium Flanders Aquifer Layers (DOV)"
    base_url = "https://www.dov.vlaanderen.be/geoserver/grondwater/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_aquifer",
        display_name="Flanders (Belgium) Aquifer Layers (DOV)",
        wcs_url="https://www.dov.vlaanderen.be/geoserver/grondwater/wms",
        coverage_id="geothermie_watervoerende_lagen",
        variable=Variable(name="aquifer_layer", units="class", data_type=DataType.CATEGORICAL,
                          description="Flanders water-bearing geological layers"),
        resolution_m=100, category="geology",
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Open (DOV)",
        citation="DOV, Watervoerende Lagen Vlaanderen",
        use_wms=True,
    )


@register("belgium_hydrostrat")
class BelgiumHydrostratConnector(NationalWCSConnector):
    slug = "belgium_hydrostrat"
    display_name = "Belgium Flanders Hydrostratigraphy (DOV)"
    base_url = "https://www.dov.vlaanderen.be/geoserver/interpretaties/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_hydrostrat",
        display_name="Flanders (Belgium) Hydrogeological Stratigraphy (DOV)",
        wcs_url="https://www.dov.vlaanderen.be/geoserver/interpretaties/wms",
        coverage_id="hydrogeologische_stratigrafie",
        variable=Variable(name="hydrostrat", units="class", data_type=DataType.CATEGORICAL,
                          description="Flanders hydrogeological stratigraphic interpretation"),
        resolution_m=100, category="geology",
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Open (DOV)",
        citation="DOV, Hydrogeologische Stratigrafie Vlaanderen",
        use_wms=True,
    )


@register("belgium_lithology")
class BelgiumLithologyConnector(NationalWCSConnector):
    slug = "belgium_lithology"
    display_name = "Belgium Flanders Lithology (DOV)"
    base_url = "https://www.dov.vlaanderen.be/geoserver/interpretaties/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="belgium_lithology",
        display_name="Flanders (Belgium) Coded Lithology (DOV)",
        wcs_url="https://www.dov.vlaanderen.be/geoserver/interpretaties/wms",
        coverage_id="gecodeerde_lithologie",
        variable=Variable(name="lithology", units="class", data_type=DataType.CATEGORICAL,
                          description="Flanders coded lithological interpretation"),
        resolution_m=100, category="geology",
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Open (DOV)", citation="DOV, Gecodeerde Lithologie Vlaanderen",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  CZECH REPUBLIC
# ═══════════════════════════════════════════════════════════════════════

@register("czech_dem")
class CzechDEMConnector(NationalWCSConnector):
    slug = "czech_dem"
    display_name = "Czech Republic DMR5G 5m"
    base_url = "https://ags.cuzk.cz/arcgis2/services/dmr5g/ImageServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="czech_dem", display_name="Czech Republic DMR5G 5m (CUZK)",
        wcs_url="https://ags.cuzk.cz/arcgis2/services/dmr5g/ImageServer/WMSServer",
        coverage_id="dmr5g", variable=ELEV_VAR, resolution_m=5,
        bbox=BoundingBox(min_lon=12.1, min_lat=48.6, max_lon=18.9, max_lat=51.1),
        license="Open (CUZK)", citation="CUZK, Digitální model reliéfu 5. generace",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  IRELAND
# ═══════════════════════════════════════════════════════════════════════

@register("ireland_soil")
class IrelandSoilConnector(NationalWCSConnector):
    slug = "ireland_soil"
    display_name = "Ireland Soil Map (Teagasc)"
    base_url = "https://gis.epa.ie/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ireland_soil", display_name="Ireland National Soil Map (EPA/Teagasc)",
        wcs_url="https://gis.epa.ie/geoserver/wms",
        coverage_id="EPA:SOIL_SISNationalSoils",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Irish soil classification 1:250,000"),
        resolution_m=250, category="soil",
        bbox=BoundingBox(min_lon=-10.5, min_lat=51.4, max_lon=-6.0, max_lat=55.4),
        license="CC-BY 4.0", citation="Teagasc / EPA, Irish Soil Information System",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  AUSTRIA
# ═══════════════════════════════════════════════════════════════════════

@register("austria_tirol_dem")
class AustriaTirolDEMConnector(NationalWCSConnector):
    slug = "austria_tirol_dem"
    display_name = "Austria Tirol DEM 1m"
    base_url = "https://gis.tirol.gv.at/arcgis/services/Service_Public/terrain/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="austria_tirol_dem", display_name="Tirol (Austria) DGM 1m LiDAR",
        wcs_url="https://gis.tirol.gv.at/arcgis/services/Service_Public/terrain/MapServer/WMSServer",
        coverage_id="0", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=10.1, min_lat=46.7, max_lon=12.8, max_lat=47.7),
        license="CC-BY 4.0", citation="Land Tirol, Digitales Geländemodell",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  PORTUGAL
# ═══════════════════════════════════════════════════════════════════════

@register("portugal_lc")
class PortugalLCConnector(NationalWCSConnector):
    slug = "portugal_lc"
    display_name = "Portugal COS Land Cover"
    base_url = "https://geo2.dgterritorio.gov.pt/geoserver/maf/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="portugal_lc", display_name="Portugal COS Land Cover (DGT)",
        wcs_url="https://geo2.dgterritorio.gov.pt/geoserver/maf/wms",
        coverage_id="MAF1951_1980",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Portuguese land use/cover (CORINE-compatible)"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=-9.5, min_lat=36.9, max_lon=-6.2, max_lat=42.2),
        license="Open (DGT)", citation="DGT, Carta de Uso e Ocupação do Solo",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  DIGITAL EARTH AFRICA (pan-African WCS)
# ═══════════════════════════════════════════════════════════════════════

@register("dea_africa_dem")
class DEAAfricaDEMConnector(NationalWCSConnector):
    slug = "dea_africa_dem"
    display_name = "DEA Africa SRTM Derivatives"
    base_url = "https://ows.digitalearth.africa"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_dem",
        display_name="Digital Earth Africa SRTM Derivatives 30m",
        wcs_url="https://ows.digitalearth.africa/wcs",
        coverage_id="srtm_deriv", variable=ELEV_VAR, resolution_m=30,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0",
        citation="Digital Earth Africa, SRTM-derived slope/MRVBF",
        crs="EPSG:6933",
    )


@register("dea_africa_lc")
class DEAAfricaLCConnector(NationalWCSConnector):
    slug = "dea_africa_lc"
    display_name = "DEA Africa Land Cover"
    base_url = "https://ows.digitalearth.africa"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_lc",
        display_name="Digital Earth Africa ESA WorldCover 10m (WCS)",
        wcs_url="https://ows.digitalearth.africa/wcs",
        coverage_id="esa_worldcover_2021", protocol_version="2.0.1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="ESA WorldCover via DEA Africa WCS"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0", citation="Digital Earth Africa / ESA WorldCover",
    )


# ═══════════════════════════════════════════════════════════════════════
#  LUXEMBOURG
# ═══════════════════════════════════════════════════════════════════════

@register("luxembourg_dem")
class LuxembourgDEMConnector(NationalWCSConnector):
    slug = "luxembourg_dem"
    display_name = "Luxembourg DTM 1m"
    base_url = "https://wms.inspire.geoportail.lu/geoserver/el/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="luxembourg_dem", display_name="Luxembourg DTM 1m LiDAR (2024)",
        wcs_url="https://wms.inspire.geoportail.lu/geoserver/el/wms",
        coverage_id="EL_ElevationGridCoverage_DTM_2024",
        variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=5.7, min_lat=49.4, max_lon=6.5, max_lat=50.2),
        license="Open (Luxembourg)", citation="ACT, Modèle Numérique de Terrain",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  LITHUANIA
# ═══════════════════════════════════════════════════════════════════════

@register("lithuania_dem")
class LithuaniaDEMConnector(NationalWCSConnector):
    slug = "lithuania_dem"
    display_name = "Lithuania DEM (INSPIRE)"
    base_url = "https://www.inspire-geoportal.lt/geoserver/el/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="lithuania_dem", display_name="Lithuania Elevation (GIS-Centras)",
        wcs_url="https://www.inspire-geoportal.lt/geoserver/el/wms",
        coverage_id="EL.SpotElevation", variable=ELEV_VAR, resolution_m=10,
        bbox=BoundingBox(min_lon=20.9, min_lat=53.9, max_lon=26.8, max_lat=56.5),
        license="Open (Lithuania)", citation="GIS-Centras, Lithuania INSPIRE Elevation",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  SCOTLAND
# ═══════════════════════════════════════════════════════════════════════

@register("scotland_lc")
class ScotlandLCConnector(NationalWCSConnector):
    slug = "scotland_lc"
    display_name = "Scotland Land Cover (NatureScot)"
    base_url = "https://ogc.nature.scot/geoserver/habitatsandspecies/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="scotland_lc",
        display_name="Scotland Habitat & Land Cover Map 2022 (NatureScot)",
        wcs_url="https://ogc.nature.scot/geoserver/habitatsandspecies/wms",
        coverage_id="csgn2021hnoa",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="EUNIS habitat classification (28 classes, AI/Sentinel-2)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=-8.6, min_lat=54.6, max_lon=-0.7, max_lat=60.9),
        license="OGL v3", citation="NatureScot / Space Intelligence, Scotland Land Cover Map",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  DENMARK SOIL
# ═══════════════════════════════════════════════════════════════════════

# Disabled: GEUS GeoServer (data.geus.dk) WAF blocks all data delivery — every
# GetMap/GetCoverage returns HTML 403/502 regardless of layer/CRS/format, though
# GetCapabilities works. Affects all four GEUS layers below. Verified 2026-05-30.
# @register("denmark_soil")
class DenmarkSoilConnector(NationalWCSConnector):
    slug = "denmark_soil"
    display_name = "Denmark Soil Map (GEUS)"
    base_url = "https://data.geus.dk/geusmap/ows/25832.jsp"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_soil",
        display_name="Denmark Jordartskort 1:200k (GEUS)",
        wcs_url="https://data.geus.dk/geusmap/ows/25832.jsp",
        coverage_id="data.geus.dk_ows",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Danish quaternary deposits / soil type classification"),
        resolution_m=200, category="soil",
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (GEUS)", citation="GEUS, Jordartskort over Danmark 1:200.000",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMAN STATE DEMs (additional)
# ═══════════════════════════════════════════════════════════════════════

@register("germany_thuringia_dem")
class GermanyThuringiaDEMConnector(NationalWCSConnector):
    slug = "germany_thuringia_dem"
    display_name = "Germany Thüringen DGM 2m"
    base_url = "https://www.geoproxy.geoportal-th.de/geoproxy/services/DGM"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_thuringia_dem",
        display_name="Thüringen (Germany) DGM2 2m LiDAR",
        wcs_url="https://www.geoproxy.geoportal-th.de/geoproxy/services/DGM",
        coverage_id="DGM2", variable=ELEV_VAR, resolution_m=2,
        bbox=BoundingBox(min_lon=9.9, min_lat=50.2, max_lon=12.7, max_lat=51.7),
        license="DL-DE/BY-2.0", citation="TLVermGeo, Digitales Geländemodell Thüringen",
        use_wms=True,
    )


@register("germany_hessen_dem")
class GermanyHessenDEMConnector(NationalWCSConnector):
    slug = "germany_hessen_dem"
    display_name = "Germany Hessen DGM"
    base_url = "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-maps.ows"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_hessen_dem",
        display_name="Hessen (Germany) DGM LiDAR",
        wcs_url="https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-maps.ows",
        coverage_id="he_dgm", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=7.8, min_lat=49.4, max_lon=10.3, max_lat=51.7),
        license="DL-DE/Zero", citation="HVBG, Digitales Geländemodell Hessen",
        crs="EPSG:25832",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NORWAY SOIL
# ═══════════════════════════════════════════════════════════════════════

@register("norway_forest")
class NorwayForestConnector(NationalWCSConnector):
    slug = "norway_forest"
    display_name = "Norway Forest Resources SR16 (NIBIO)"
    base_url = "https://wms.nibio.no/cgi-bin/sr16"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_forest",
        display_name="Norway SR16 Forest Tree Height 16m (NIBIO)",
        wcs_url="https://wms.nibio.no/cgi-bin/sr16",
        coverage_id="SRRHOYDEM",
        variable=Variable(name="tree_height", units="m", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 40),
                          description="Forest tree height — controls interception and snow trapping"),
        resolution_m=16, category="vegetation",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NIBIO, SR16 Forest Resource Map",
        use_wms=True,
    )


@register("norway_tree_species")
class NorwayTreeSpeciesConnector(NationalWCSConnector):
    slug = "norway_tree_species"
    display_name = "Norway Tree Species SR16 (NIBIO)"
    base_url = "https://wms.nibio.no/cgi-bin/sr16"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_tree_species",
        display_name="Norway SR16 Dominant Tree Species (NIBIO)",
        wcs_url="https://wms.nibio.no/cgi-bin/sr16",
        coverage_id="SRRTRESLAG",
        variable=Variable(name="tree_species", units="class", data_type=DataType.CATEGORICAL,
                          description="Dominant tree species (spruce/pine/deciduous)"),
        resolution_m=16, category="vegetation",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NIBIO, SR16 Forest Resource Map",
        use_wms=True,
    )


@register("norway_tree_volume")
class NorwayTreeVolumeConnector(NationalWCSConnector):
    slug = "norway_tree_volume"
    display_name = "Norway Timber Volume SR16 (NIBIO)"
    base_url = "https://wms.nibio.no/cgi-bin/sr16"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_tree_volume",
        display_name="Norway SR16 Timber Volume (NIBIO)",
        wcs_url="https://wms.nibio.no/cgi-bin/sr16",
        coverage_id="SRRVOLMB",
        variable=Variable(name="timber_volume", units="m3/ha", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 500),
                          description="Standing timber volume — controls biomass and canopy density"),
        resolution_m=16, category="vegetation",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NIBIO, SR16 Forest Resource Map",
        use_wms=True,
    )


@register("norway_vegetation")
class NorwayVegetationConnector(NationalWCSConnector):
    slug = "norway_vegetation"
    display_name = "Norway Vegetation Map (NIBIO)"
    base_url = "https://wms.nibio.no/cgi-bin/vegetasjon"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_vegetation",
        display_name="Norway Vegetation Type Map (NIBIO)",
        wcs_url="https://wms.nibio.no/cgi-bin/vegetasjon",
        coverage_id="VEGETASJON",
        variable=Variable(name="vegetation_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian vegetation type classification"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NIBIO, Vegetasjonskartet",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMANY — additional state DEMs
# ═══════════════════════════════════════════════════════════════════════

# Disabled: WCS GetCoverage is broken server-side — GetCapabilities/DescribeCoverage
# return 200, but every in-extent GetCoverage returns a bare 500. Verified 2026-05-30.
# @register("germany_sachsen_anhalt_dem")
class GermanySachsenAnhaltDEMConnector(NationalWCSConnector):
    slug = "germany_sachsen_anhalt_dem"
    display_name = "Germany Sachsen-Anhalt DGM1 1m"
    base_url = "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DGM1_WCS_OpenData/guest"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_sachsen_anhalt_dem",
        display_name="Sachsen-Anhalt (Germany) DGM1 1m LiDAR",
        wcs_url="https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DGM1_WCS_OpenData/guest",
        coverage_id="Coverage1", variable=ELEV_VAR, resolution_m=1,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=10.6, min_lat=50.9, max_lon=13.2, max_lat=53.1),
        license="DL-DE/Zero",
        citation="LVermGeo Sachsen-Anhalt, Digitales Geländemodell 1m",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  SPAIN — geology/lithology
# ═══════════════════════════════════════════════════════════════════════

@register("spain_lithology")
class SpainLithologyConnector(NationalWCSConnector):
    slug = "spain_lithology"
    display_name = "Spain Lithology 1:1M (IGME)"
    base_url = "https://mapas.igme.es/gis/services/Cartografia_Geologica/IGME_Geologico_1M/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_lithology",
        display_name="Spain Geological Map 1:1M (IGME)",
        wcs_url="https://mapas.igme.es/gis/services/Cartografia_Geologica/IGME_Geologico_1M/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="lithology", units="class", data_type=DataType.CATEGORICAL,
                          description="Spanish lithological/geological classification 1:1,000,000"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="Open (IGME)", citation="IGME, Mapa Geológico de España 1:1.000.000",
        use_wms=True,
    )


@register("spain_hydrogeology")
class SpainHydrogeologyConnector(NationalWCSConnector):
    slug = "spain_hydrogeology"
    display_name = "Spain Hydrogeology (IGME)"
    base_url = "https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Hidrogeologico_1M/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_hydrogeology",
        display_name="Spain Hydrogeological Map 1:1M (IGME)",
        wcs_url="https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Hidrogeologico_1M/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="hydrogeology", units="class", data_type=DataType.CATEGORICAL,
                          description="Spanish hydrogeological classification (aquifer types)"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="Open (IGME)", citation="IGME, Mapa Hidrogeológico de España 1:1.000.000",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMANY — more state DEMs
# ═══════════════════════════════════════════════════════════════════════

@register("germany_mv_dem")
class GermanyMVDEMConnector(NationalWCSConnector):
    slug = "germany_mv_dem"
    display_name = "Germany Mecklenburg-Vorpommern DGM 1m"
    base_url = "https://www.geodaten-mv.de/dienste/dgm_wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_mv_dem",
        display_name="Mecklenburg-Vorpommern (Germany) DGM 1m",
        wcs_url="https://www.geodaten-mv.de/dienste/dgm_wcs",
        coverage_id="mv_dgm", variable=ELEV_VAR, resolution_m=1,
        protocol_version="2.0.1", crs="EPSG:25833",
        bbox=BoundingBox(min_lon=10.6, min_lat=53.1, max_lon=14.4, max_lat=54.7),
        license="DL-DE/Zero",
        citation="LAiV M-V, Digitales Geländemodell Mecklenburg-Vorpommern",
    )


@register("germany_bb_dem")
class GermanyBBDEMConnector(NationalWCSConnector):
    slug = "germany_bb_dem"
    display_name = "Germany Brandenburg DGM 1m"
    base_url = "https://isk.geobasis-bb.de/ows/dgm_wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_bb_dem",
        display_name="Brandenburg (Germany) DGM 1m LiDAR",
        wcs_url="https://isk.geobasis-bb.de/ows/dgm_wcs",
        coverage_id="bb_dgm", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=11.3, min_lat=51.4, max_lon=14.8, max_lat=53.6),
        license="DL-DE/Zero",
        citation="LGB Brandenburg, Digitales Geländemodell 1m",
        crs="EPSG:25833",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMANY — city-states
# ═══════════════════════════════════════════════════════════════════════

@register("germany_hamburg_dem")
class GermanyHamburgDEMConnector(NationalWCSConnector):
    slug = "germany_hamburg_dem"
    display_name = "Germany Hamburg DGM 1m"
    base_url = "https://geodienste.hamburg.de/HH_WMS_DGM1"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_hamburg_dem",
        display_name="Hamburg (Germany) DGM 1m",
        wcs_url="https://geodienste.hamburg.de/HH_WMS_DGM1",
        coverage_id="WMS_DGM1_HAMBURG", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=9.7, min_lat=53.4, max_lon=10.3, max_lat=53.7),
        license="DL-DE/BY-2.0",
        citation="LGV Hamburg, Digitales Geländemodell 1m",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  FRANCE — land cover
# ═══════════════════════════════════════════════════════════════════════

@register("france_lc")
class FranceLCConnector(NationalWCSConnector):
    slug = "france_lc"
    display_name = "France OCS GE Land Cover"
    base_url = "https://data.geopf.fr/wms-r/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="france_lc",
        display_name="France OCS GE Land Cover (IGN Geoplateforme)",
        wcs_url="https://data.geopf.fr/wms-r/wms",
        coverage_id="LANDCOVER.CHA12_FR",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="French national land cover change map (OCS GE)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
        license="Open Licence (Etalab)", citation="IGN, OCS GE (Geoplateforme)",
        # bbox centroid / first land anchor (Switzerland) falls outside France's
        # data mask, and OCS GE has rural gaps. Pin Paris so the check lands on
        # dense, reliably-populated land cover.
        health_anchor=(2.35, 48.86),
        use_wms=True, wms_version="1.3.0",
    )


# ═══════════════════════════════════════════════════════════════════════
#  UK — geology (BGS)
# ═══════════════════════════════════════════════════════════════════════

@register("uk_bgs_geology")
class UKBGSGeologyConnector(NationalWCSConnector):
    slug = "uk_bgs_geology"
    display_name = "UK Bedrock Geology (BGS)"
    base_url = "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_bgs_geology",
        display_name="UK BGS Bedrock Geology 1:50k",
        wcs_url="https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer",
        coverage_id="BGS.50k.Bedrock",
        variable=Variable(name="bedrock", units="class", data_type=DataType.CATEGORICAL,
                          description="British Geological Survey bedrock lithology classification"),
        resolution_m=50, category="geology",
        bbox=BoundingBox(min_lon=-8.6, min_lat=49.8, max_lon=2.0, max_lat=60.9),
        license="OGL v3", citation="British Geological Survey, DiGMapGB-50",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  FINLAND — land cover (SYKE CORINE)
# ═══════════════════════════════════════════════════════════════════════

@register("finland_lc")
class FinlandLCConnector(NationalWCSConnector):
    slug = "finland_lc"
    display_name = "Finland CORINE Land Cover (SYKE)"
    base_url = "https://paikkatiedot.ymparisto.fi/geoserver/inspire_lc/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="finland_lc",
        display_name="Finland CORINE Land Cover 2018 20m (SYKE)",
        wcs_url="https://paikkatiedot.ymparisto.fi/geoserver/inspire_lc/wms",
        coverage_id="LC.LandCoverRaster.2000",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Finnish CORINE land cover 2018 (20m raster)"),
        resolution_m=20, category="land_cover",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="SYKE, CORINE Land Cover Finland 2018",
    )


# ═══════════════════════════════════════════════════════════════════════
#  DENMARK — geology/groundwater
# ═══════════════════════════════════════════════════════════════════════

# Disabled: GEUS GeoServer WAF blocks all data delivery (see denmark_soil note).
# @register("denmark_geology")
class DenmarkGeologyConnector(NationalWCSConnector):
    slug = "denmark_geology"
    display_name = "Denmark Groundwater (GEUS)"
    base_url = "https://data.geus.dk/geusmap/ows/25832.jsp"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_geology",
        display_name="Denmark Groundwater Map (GEUS)",
        wcs_url="https://data.geus.dk/geusmap/ows/25832.jsp",
        coverage_id="grundvand",
        variable=Variable(name="groundwater", units="class", data_type=DataType.CATEGORICAL,
                          description="Danish groundwater mapping / aquifer classification"),
        resolution_m=200, category="geology",
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (GEUS)", citation="GEUS, Grundvandskortlægning",
    )


# Disabled: GEUS GeoServer WAF blocks all data delivery (see denmark_soil note).
# @register("denmark_nitrate")
class DenmarkNitrateConnector(NationalWCSConnector):
    slug = "denmark_nitrate"
    display_name = "Denmark Groundwater Nitrate (GEUS)"
    base_url = "https://data.geus.dk/geusmap/ows/25832.jsp"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_nitrate",
        display_name="Denmark Groundwater Nitrate >2mg/L (GEUS)",
        wcs_url="https://data.geus.dk/geusmap/ows/25832.jsp",
        coverage_id="nitrat_2mg_and_above",
        variable=Variable(name="nitrate", units="mg/L", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 200),
                          description="Groundwater nitrate concentration — water quality indicator"),
        resolution_m=5000, category="hydrology",
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (GEUS)", citation="GEUS, Grundvand Nitrat",
    )


# Disabled: GEUS GeoServer WAF blocks all data delivery (see denmark_soil note).
# @register("denmark_water_supply")
class DenmarkWaterSupplyConnector(NationalWCSConnector):
    slug = "denmark_water_supply"
    display_name = "Denmark Water Supply Areas (GEUS)"
    base_url = "https://data.geus.dk/geusmap/ows/25832.jsp"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_water_supply",
        display_name="Denmark Water Supply Zones (GEUS)",
        wcs_url="https://data.geus.dk/geusmap/ows/25832.jsp",
        coverage_id="danske_vandforsyningsomraader_gruk",
        variable=Variable(name="water_supply", units="class", data_type=DataType.CATEGORICAL,
                          description="Danish water supply areas/zones"),
        resolution_m=1000, category="hydrology",
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (GEUS)", citation="GEUS, Vandforsyningsområder",
    )


# ═══════════════════════════════════════════════════════════════════════
#  ESTONIA — land cover
# ═══════════════════════════════════════════════════════════════════════

@register("estonia_lc")
class EstoniaLCConnector(NationalWCSConnector):
    slug = "estonia_lc"
    display_name = "Estonia Land Cover (Maa-amet)"
    base_url = "https://kaart.maaamet.ee/wms/alus-geo"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="estonia_lc",
        display_name="Estonia Land Cover (Maa-amet Base Map)",
        wcs_url="https://kaart.maaamet.ee/wms/alus-geo",
        coverage_id="pohi_vr2",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Estonian base map land cover classification"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=21.8, min_lat=57.5, max_lon=28.2, max_lat=59.7),
        license="Estonian Open Data",
        citation="Maa-amet, Estonian Land Board Base Map",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NORWAY — surficial geology (NGU)
# ═══════════════════════════════════════════════════════════════════════

@register("norway_geology")
class NorwayGeologyConnector(NationalWCSConnector):
    slug = "norway_geology"
    display_name = "Norway Surficial Geology (NGU)"
    base_url = "https://geo.ngu.no/mapserver/LosmasserWMS2"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_geology",
        display_name="Norway Quaternary Deposits / Surficial Geology (NGU)",
        wcs_url="https://geo.ngu.no/mapserver/LosmasserWMS2",
        coverage_id="LosmasserWMS2",
        variable=Variable(name="surficial_geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian surficial deposits (till, marine clay, peat, etc.)"),
        resolution_m=50, category="geology",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="CC-BY 4.0 (NLOD)", citation="NGU, Løsmassekart (Surficial Geology Map of Norway)",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NETHERLANDS — land cover
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  ITALY — DEM slope/aspect derivatives
# ═══════════════════════════════════════════════════════════════════════

# @register("italy_slope")  # Disabled: tinitaly.pi.ingv.it unreachable
class ItalySlopeConnector(NationalWCSConnector):
    slug = "italy_slope"
    display_name = "Italy Slope (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_slope",
        display_name="Italy Slope 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1:tinitaly_slope",
        variable=Variable(name="slope", units="degrees", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 90),
                          description="Terrain slope — controls runoff velocity and erosion"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 slope",
    )


# @register("italy_aspect")  # Disabled: tinitaly.pi.ingv.it unreachable
class ItalyAspectConnector(NationalWCSConnector):
    slug = "italy_aspect"
    display_name = "Italy Aspect (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_aspect",
        display_name="Italy Aspect 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1:tinitaly_hsv",
        variable=Variable(name="aspect", units="degrees", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 360),
                          description="Terrain aspect — controls solar radiation and snowmelt"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 aspect",
    )


# @register("italy_hillshade")  # Disabled: tinitaly.pi.ingv.it unreachable
class ItalyHillshadeConnector(NationalWCSConnector):
    slug = "italy_hillshade"
    display_name = "Italy Hillshade (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_hillshade",
        display_name="Italy Hillshade 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1:tinitaly_hshd",
        variable=Variable(name="hillshade", units="index", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 255),
                          description="Terrain hillshade — visualizes topographic features"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 hillshade",
    )


# @register("italy_skyview")  # Disabled: tinitaly.pi.ingv.it unreachable
class ItalySkyviewConnector(NationalWCSConnector):
    slug = "italy_skyview"
    display_name = "Italy Sky-View Factor (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_skyview",
        display_name="Italy Sky-View Factor 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1:tinitaly_svf_2d5",
        variable=Variable(name="sky_view_factor", units="fraction", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 1),
                          description="Sky-view factor — controls solar radiation and ET"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 SVF",
    )


# ═══════════════════════════════════════════════════════════════════════
#  FRANCE — geology (BRGM)
# ═══════════════════════════════════════════════════════════════════════

# Disabled: BRGM geoservices is a *styled* WMS of the scanned 1:1M geological
# map. It rejects image/geotiff (msWMSLoadGetMapParams: Unsupported); the
# image/tiff fallback returns a single-band float raster of RGB-derived pixel
# values (a 0–255 "distribution" of scan colours), not classified geology codes.
# It also renders nothing below ~0.2° bbox — larger than the health-check
# polygon's 0.05° cap (test_geometry._MAX_HALF_SIZE) — so it can't be coaxed
# healthy via resolution_m either. Verified 2026-05-30.
# Re-checked 2026-05-31 against the color_map mapper: still unrecoverable. A 1°
# GetMap render returns 254 distinct colors that are all near-clones of each other
# ((247,57,20)/(247,61,20)/(247,53,20)/...) — JPEG/anti-alias noise from the
# scanned paper map, with no flat fills, so no color->class table can be built.
# (Contrast france_lithology below, a vector layer that IS injective and revived.)
# Re-enable path: use a class-coded BRGM source (e.g. the GeoServer WFS geol unit
# features, or a rasterized lithology grid) with a code->name legend, then
# restore @register.
# @register("france_geology")
class FranceGeologyConnector(NationalWCSConnector):
    slug = "france_geology"
    display_name = "France Geological Map (BRGM)"
    base_url = "https://geoservices.brgm.fr/geologie"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="france_geology",
        display_name="France Geological Map 1:1M (BRGM)",
        wcs_url="https://geoservices.brgm.fr/geologie",
        coverage_id="SCAN_F_GEOL1M",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="French geological/lithological classification 1:1M"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
        license="Open (BRGM)", citation="BRGM, Carte Géologique de la France",
        use_wms=True,
    )


# Re-enabled 2026-05-31 via the color_map symbology mapper. Unlike france_geology
# (a scanned raster, see note above), LITHO_1M_SIMPLIFIEE is a *vector* layer
# rendered with flat fills: its WMS GetStyles SLD is a unique-value renderer on
# CODE_GEOL with 11 classes -> 11 distinct, injective fill colors (verified to
# exactly match the rendered PNG; polygon-boundary anti-aliasing is <2% of pixels
# and falls to nodata). The old image/tiff fallback returned all-nodata, so the
# connector now requests image/png up front (gated on color_map in national_wcs)
# and inverts the symbology back to CODE_GEOL. Lithology controls permeability —
# a useful subsurface/hydrogeology attribute. Live-verified over the Paris Basin.
@register("france_lithology")
class FranceLithologyConnector(NationalWCSConnector):
    slug = "france_lithology"
    display_name = "France Lithology (BRGM)"
    base_url = "https://geoservices.brgm.fr/geologie"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="france_lithology",
        display_name="France Simplified Lithology 1:1M (BRGM)",
        wcs_url="https://geoservices.brgm.fr/geologie",
        coverage_id="LITHO_1M_SIMPLIFIEE",
        variable=Variable(
            name="lithology", units="class", data_type=DataType.CATEGORICAL,
            description=(
                "Simplified lithological classification (BRGM CODE_GEOL, controls "
                "permeability): 1 Argiles/clays, 2 Calcaire-marnes-gypse/limestone, "
                "3 Craie/chalk, 5 Gres/sandstone, 6 Sables/sands, 7 Basaltes-rhyolites, "
                "8 Granites, 9 Ophiolites, 10 Gneiss, 11 Micaschistes, 12 Schistes-gres"
            ),
        ),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
        license="Open (BRGM)", citation="BRGM, Lithologie Simplifiée",
        use_wms=True,
        # CODE_GEOL anchor at the Paris Basin (limestone/sands) — renders a clean
        # dominant class at the health-check's ~0.1deg box (the >0.2deg blank-render
        # limitation in the france_geology note applies only to the scanned raster).
        health_anchor=(2.35, 48.85),
        # RGB fill -> CODE_GEOL, from the layer's WMS GetStyles SLD (injective).
        color_map={
            (212, 255, 191): 1,   # Argiles (clays)
            (115, 179, 255): 2,   # Calcaire, marnes et gypse (limestone/marl/gypsum)
            (191, 209, 255): 3,   # Craie (chalk)
            (245, 201, 122): 5,   # Grès (sandstone)
            (255, 255, 115): 6,   # Sables (sands)
            (0, 76, 168): 7,      # Basaltes et rhyolites
            (255, 0, 0): 8,       # Granites
            (0, 135, 0): 9,       # Ophiolites
            (255, 191, 232): 10,  # Gneiss
            (255, 235, 191): 11,  # Micaschistes
            (138, 138, 69): 12,   # Schistes et grès
        },
    )


# ═══════════════════════════════════════════════════════════════════════
#  ESTONIA — geology
# ═══════════════════════════════════════════════════════════════════════

@register("estonia_geology")
class EstoniaGeologyConnector(NationalWCSConnector):
    slug = "estonia_geology"
    display_name = "Estonia Geology (Maa-amet)"
    base_url = "https://kaart.maaamet.ee/wms/geoloogia"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="estonia_geology",
        display_name="Estonia Geological Map (Maa-amet)",
        wcs_url="https://kaart.maaamet.ee/wms/geoloogia",
        coverage_id="MAMT-GEOLOOGIA-S",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Estonian geological/surficial deposits classification"),
        resolution_m=50, category="geology",
        bbox=BoundingBox(min_lon=21.8, min_lat=57.5, max_lon=28.2, max_lat=59.7),
        license="Estonian Open Data", citation="Maa-amet, Geological Map of Estonia",
        use_wms=True,
        crs="EPSG:3301",
    )


# ═══════════════════════════════════════════════════════════════════════

@register("uk_soilscapes")
class UKSoilscapesConnector(NationalWCSConnector):
    slug = "uk_soilscapes"
    display_name = "UK Soilscapes (Cranfield)"
    base_url = "https://www.landis.org.uk/arcgis/services/UKSoilObservatory/Soilscapes_Cranfield/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_soilscapes",
        display_name="UK Soilscapes Soil Map (Cranfield/LandIS)",
        # OGC services path (not /rest/services/, which returns the HTML service page).
        wcs_url="https://www.landis.org.uk/arcgis/services/UKSoilObservatory/Soilscapes_Cranfield/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="UK soil landscape classification (27 types)"),
        resolution_m=100, category="soil",
        bbox=BoundingBox(min_lon=-8.6, min_lat=49.8, max_lon=2.0, max_lat=60.9),
        license="Open (LandIS)", citation="Cranfield University, Soilscapes",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  SWITZERLAND — soil erosion + organic soils
# ═══════════════════════════════════════════════════════════════════════

@register("swiss_soil_depth")
class SwissSoilDepthConnector(NationalWCSConnector):
    slug = "swiss_soil_depth"
    display_name = "Switzerland Soil Depth"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_soil_depth",
        display_name="Switzerland Soil Depth/Rootability (BLW)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.blw.bodeneignung-gruendigkeit",
        variable=Variable(name="soil_depth", units="class", data_type=DataType.CATEGORICAL,
                          description="Swiss soil depth/rootability classification"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="BLW, Bodeneignung Gründigkeit",
        use_wms=True,
    )


@register("swiss_erosion_flow")
class SwissErosionFlowConnector(NationalWCSConnector):
    slug = "swiss_erosion_flow"
    display_name = "Switzerland Erosion Flow Paths"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_erosion_flow",
        display_name="Switzerland Erosion Flow Path Map (BLW)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.blw.erosion-fliesswegkarte",
        variable=Variable(name="erosion_flow", units="class", data_type=DataType.CATEGORICAL,
                          description="Erosion flow paths — runoff concentration routes"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="BLW, Erosion Fliesswegkarte",
        use_wms=True,
    )


@register("swiss_groundwater")
class SwissGroundwaterConnector(NationalWCSConnector):
    slug = "swiss_groundwater"
    display_name = "Switzerland Groundwater Bodies"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_groundwater",
        display_name="Switzerland Groundwater Bodies (BAFU)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.bafu.grundwasserkoerper",
        variable=Variable(name="groundwater_body", units="class", data_type=DataType.CATEGORICAL,
                          description="Swiss groundwater body delineation"),
        resolution_m=100, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="BAFU, Grundwasserkörper",
        use_wms=True,
    )


@register("swiss_slope_30")
class SwissSlope30Connector(NationalWCSConnector):
    slug = "swiss_slope_30"
    display_name = "Switzerland Slope >30° (swisstopo)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_slope_30",
        display_name="Switzerland Areas with Slope >30° (swisstopo)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.swisstopo.hangneigung-ueber_30",
        variable=Variable(name="steep_slope", units="class", data_type=DataType.CATEGORICAL,
                          description="Areas with slope >30° — high erosion/landslide risk"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="swisstopo, Hangneigung über 30°",
        use_wms=True,
    )


@register("swiss_permafrost")
class SwissPermafrostConnector(NationalWCSConnector):
    slug = "swiss_permafrost"
    display_name = "Switzerland Permafrost (BAFU)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_permafrost",
        display_name="Switzerland Permafrost Extent (BAFU)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.bafu.permafrost",
        variable=Variable(name="permafrost", units="class", data_type=DataType.CATEGORICAL,
                          description="Swiss permafrost extent — controls subsurface flow"),
        resolution_m=25, category="cryosphere",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="BAFU, Permafrost",
        use_wms=True,
    )


@register("swiss_stream_order")
class SwissStreamOrderConnector(NationalWCSConnector):
    slug = "swiss_stream_order"
    display_name = "Switzerland Stream Order (BAFU)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_stream_order",
        display_name="Switzerland Strahler Stream Order (BAFU)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.bafu.flussordnungszahlen-strahler",
        variable=Variable(name="stream_order", units="order", data_type=DataType.CONTINUOUS,
                          valid_range=(1, 9),
                          description="Strahler stream order — classifies river network hierarchy"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)",
        citation="BAFU, Flussordnungszahlen nach Strahler",
        use_wms=True,
    )


@register("swiss_surface_runoff")
class SwissSurfaceRunoffConnector(NationalWCSConnector):
    slug = "swiss_surface_runoff"
    display_name = "Switzerland Surface Runoff Hazard"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_surface_runoff",
        display_name="Switzerland Surface Runoff Hazard Map (BAFU)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.bafu.gefaehrdungskarte-oberflaechenabfluss",
        variable=Variable(name="surface_runoff_hazard", units="class", data_type=DataType.CATEGORICAL,
                          description="Surface runoff hazard — pluvial flood risk mapping"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)",
        citation="BAFU, Gefährdungskarte Oberflächenabfluss",
        use_wms=True,
    )


@register("swiss_glacier")
class SwissGlacierConnector(NationalWCSConnector):
    slug = "swiss_glacier"
    display_name = "Switzerland Glacier Extent (swisstopo)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_glacier",
        display_name="Switzerland Glacier Extent (swisstopo)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.swisstopo.geologie-gletscherausdehnung",
        variable=Variable(name="glacier_extent", units="class", data_type=DataType.CATEGORICAL,
                          description="Swiss glacier extent — controls glacial melt contribution"),
        resolution_m=25, category="cryosphere",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="swisstopo, Gletscherausdehnung",
        use_wms=True,
    )


@register("swiss_lakes")
class SwissLakesConnector(NationalWCSConnector):
    slug = "swiss_lakes"
    display_name = "Switzerland Lakes (BAFU)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_lakes",
        display_name="Switzerland Lakes (BAFU vec25)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.bafu.vec25-seen",
        variable=Variable(name="lakes", units="class", data_type=DataType.CATEGORICAL,
                          description="Swiss lake bodies — surface water storage"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)", citation="BAFU, vec25 Seen",
        use_wms=True,
    )


@register("swiss_rivers")
class SwissRiversConnector(NationalWCSConnector):
    slug = "swiss_rivers"
    display_name = "Switzerland River Network (swisstopo)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_rivers",
        display_name="Switzerland River/Stream Network (swissTLM3D)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.swisstopo.swisstlm3d-gewaessernetz",
        variable=Variable(name="river_network", units="class", data_type=DataType.CATEGORICAL,
                          description="Swiss river and stream network (swissTLM3D)"),
        resolution_m=10, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)",
        citation="swisstopo, swissTLM3D Gewässernetz",
        use_wms=True,
    )


@register("swiss_water_connect")
class SwissWaterConnectConnector(NationalWCSConnector):
    slug = "swiss_water_connect"
    display_name = "Switzerland Water Body Connectivity (BLW)"
    base_url = "https://wms.geo.admin.ch"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="swiss_water_connect",
        display_name="Switzerland Water Body Connectivity Map (BLW)",
        wcs_url="https://wms.geo.admin.ch",
        coverage_id="ch.blw.gewaesseranschlusskarte",
        variable=Variable(name="water_connectivity", units="class", data_type=DataType.CATEGORICAL,
                          description="Agricultural parcel connectivity to water bodies"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (geo.admin.ch)",
        citation="BLW, Gewässeranschlusskarte",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NETHERLANDS — DSM (surface model)
# ═══════════════════════════════════════════════════════════════════════

@register("netherlands_dsm")
class NetherlandsDSMConnector(NationalWCSConnector):
    slug = "netherlands_dsm"
    display_name = "Netherlands AHN4 DSM 0.5m"
    base_url = "https://service.pdok.nl/rws/ahn/wcs/v1_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_dsm", display_name="Netherlands AHN4 DSM 0.5m (PDOK)",
        wcs_url="https://service.pdok.nl/rws/ahn/wcs/v1_0",
        coverage_id="dsm_05m", variable=ELEV_VAR, resolution_m=0.5,
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="Rijkswaterstaat, AHN4 Digital Surface Model",
    )


# ═══════════════════════════════════════════════════════════════════════
#  SPAIN — quaternary geology
# ═══════════════════════════════════════════════════════════════════════

@register("spain_quaternary")
class SpainQuaternaryConnector(NationalWCSConnector):
    slug = "spain_quaternary"
    display_name = "Spain Quaternary Geology (IGME)"
    base_url = "https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Cuaternario_1M/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_quaternary",
        display_name="Spain Quaternary Geology 1:1M (IGME)",
        wcs_url="https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Cuaternario_1M/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="quaternary", units="class", data_type=DataType.CATEGORICAL,
                          description="Spanish quaternary deposits — controls surficial permeability"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="Open (IGME)", citation="IGME, Mapa Geológico del Cuaternario 1:1M",
        crs="EPSG:25830",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  DENMARK — terrain products
# ═══════════════════════════════════════════════════════════════════════

@register("denmark_terrain")
class DenmarkTerrainConnector(NationalWCSConnector):
    slug = "denmark_terrain"
    display_name = "Denmark DHM Terrain (SDFI)"
    base_url = "https://api.dataforsyningen.dk/dhm_DAF"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_terrain",
        display_name="Denmark DHM Terrain Products (SDFI)",
        wcs_url="https://api.dataforsyningen.dk/dhm_DAF",
        coverage_id="dhm_terraen_skyggekort",
        variable=Variable(name="terrain_shade", units="index", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 255),
                          description="Danish terrain hillshade — visualizes topographic features"),
        resolution_m=0.4, category="elevation",
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (Denmark)", citation="SDFI, Danmarks Højdemodel Terrain",
        auth_token_env="CAS_DATAFORSYNINGEN_TOKEN",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  UK — WFD catchments
# ═══════════════════════════════════════════════════════════════════════

# Disabled: EA GeoServer cannot locate the backing store for this layer on any
# endpoint variant (GetMap → IllegalStateException "Could not locate a layer").
# Server-side breakage; not fixable client-side. Verified 2026-05-30.
# @register("uk_wfd_catchments")
class UKWFDCatchmentsConnector(NationalWCSConnector):
    slug = "uk_wfd_catchments"
    display_name = "UK WFD River Catchments (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/wfd-river-waterbody-catchments/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_wfd_catchments",
        display_name="UK WFD River Waterbody Catchments (EA)",
        wcs_url="https://environment.data.gov.uk/spatialdata/wfd-river-waterbody-catchments/wms",
        coverage_id="WFD_River_Water_Body_Catchments_Cycle_1",
        variable=Variable(name="catchment", units="class", data_type=DataType.CATEGORICAL,
                          description="Water Framework Directive river waterbody catchments"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3", citation="Environment Agency, WFD River Waterbody Catchments",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  FINLAND — hydrology catchments
# ═══════════════════════════════════════════════════════════════════════

@register("finland_catchments")
class FinlandCatchmentsConnector(NationalWCSConnector):
    slug = "finland_catchments"
    display_name = "Finland Drainage Basins (SYKE)"
    base_url = "https://paikkatiedot.ymparisto.fi/geoserver/inspire_hy/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="finland_catchments",
        display_name="Finland Drainage Basins (SYKE INSPIRE)",
        wcs_url="https://paikkatiedot.ymparisto.fi/geoserver/inspire_hy/wms",
        coverage_id="HY.PhysicalWaters.Catchments.DrainageBasin",
        variable=Variable(name="drainage_basin", units="class", data_type=DataType.CATEGORICAL,
                          description="Finnish drainage basin delineations (INSPIRE)"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="SYKE, Finnish Drainage Basins",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  SPAIN — hydrography
# ═══════════════════════════════════════════════════════════════════════

@register("spain_hydro")
class SpainHydroConnector(NationalWCSConnector):
    slug = "spain_hydro"
    display_name = "Spain Hydrography (IDEE)"
    base_url = "https://servicios.idee.es/wms-inspire/hidrografia"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_hydro",
        display_name="Spain Water Bodies (IDEE INSPIRE)",
        wcs_url="https://servicios.idee.es/wms-inspire/hidrografia",
        coverage_id="HY.PhysicalWaters.Waterbodies",
        variable=Variable(name="water_bodies", units="class", data_type=DataType.CATEGORICAL,
                          description="Spanish water bodies (rivers, lakes, reservoirs)"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="CC-BY 4.0", citation="IGN/IDEE, INSPIRE Hydrography",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  UK — historic flood map
# ═══════════════════════════════════════════════════════════════════════

@register("uk_historic_flood")
class UKHistoricFloodConnector(NationalWCSConnector):
    slug = "uk_historic_flood"
    display_name = "UK Historic Flood Map (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/historic-flood-map/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_historic_flood",
        display_name="UK Historic Flood Map (Environment Agency)",
        wcs_url="https://environment.data.gov.uk/spatialdata/historic-flood-map/wms",
        coverage_id="Historic_Flood_Map",
        variable=Variable(name="historic_flood", units="class", data_type=DataType.CATEGORICAL,
                          description="Areas with historic flood records — identifies flood-prone zones"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3", citation="Environment Agency, Historic Flood Map",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NETHERLANDS — crop parcels
# ═══════════════════════════════════════════════════════════════════════

@register("netherlands_crops")
class NetherlandsCropsConnector(NationalWCSConnector):
    slug = "netherlands_crops"
    display_name = "Netherlands Crop Parcels (PDOK)"
    base_url = "https://service.pdok.nl/rvo/brpgewaspercelen/wms/v1_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_crops",
        display_name="Netherlands BRP Crop Parcels (PDOK/RVO)",
        wcs_url="https://service.pdok.nl/rvo/brpgewaspercelen/wms/v1_0",
        coverage_id="BrpGewas",
        variable=Variable(name="crop_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Dutch agricultural crop parcels — annual crop type mapping"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="RVO, BRP Gewaspercelen via PDOK",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  UK — groundwater and drinking water protection
# ═══════════════════════════════════════════════════════════════════════

@register("uk_groundwater_bodies")
class UKGroundwaterBodiesConnector(NationalWCSConnector):
    slug = "uk_groundwater_bodies"
    display_name = "UK WFD Groundwater Bodies (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/wfd-groundwater-bodies-cycle-2/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_groundwater_bodies",
        display_name="UK WFD Groundwater Bodies (EA)",
        wcs_url="https://environment.data.gov.uk/spatialdata/wfd-groundwater-bodies-cycle-2/wms",
        coverage_id="WFD_Groundwater_Bodies_Cycle_2",
        variable=Variable(name="groundwater_body", units="class", data_type=DataType.CATEGORICAL,
                          description="WFD designated groundwater bodies — aquifer delineation"),
        resolution_m=100, category="hydrology",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3", citation="Environment Agency, WFD Groundwater Bodies Cycle 2",
        use_wms=True,
    )


@register("uk_source_protection")
class UKSourceProtectionConnector(NationalWCSConnector):
    slug = "uk_source_protection"
    display_name = "UK Source Protection Zones (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/source-protection-zones-merged/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_source_protection",
        display_name="UK Source Protection Zones (EA)",
        wcs_url="https://environment.data.gov.uk/spatialdata/source-protection-zones-merged/wms",
        coverage_id="Source_Protection_Zones_Merged",
        variable=Variable(name="spz", units="class", data_type=DataType.CATEGORICAL,
                          description="Groundwater source protection zones (inner/outer/total catchment)"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3", citation="Environment Agency, Source Protection Zones",
        use_wms=True,
    )


@register("uk_drinking_water")
class UKDrinkingWaterConnector(NationalWCSConnector):
    slug = "uk_drinking_water"
    display_name = "UK Drinking Water Protected Areas (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/drinking-water-protected-areas-surface-water/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_drinking_water",
        display_name="UK Drinking Water Protected Areas (EA)",
        wcs_url="https://environment.data.gov.uk/spatialdata/drinking-water-protected-areas-surface-water/wms",
        coverage_id="Drinking_Water_Protected_Areas_Surface_Water",
        variable=Variable(name="drinking_water", units="class", data_type=DataType.CATEGORICAL,
                          description="Drinking water protected areas — surface water abstraction zones"),
        resolution_m=50, category="hydrology",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3",
        citation="Environment Agency, Drinking Water Protected Areas",
        use_wms=True,
    )


@register("uk_agri_land_class")
class UKAgriLandClassConnector(NationalWCSConnector):
    slug = "uk_agri_land_class"
    display_name = "UK Agricultural Land Classification (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/agricultural-land-classification-provisional-england/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_agri_land_class",
        display_name="UK Agricultural Land Classification (Provisional, EA)",
        wcs_url="https://environment.data.gov.uk/spatialdata/agricultural-land-classification-provisional-england/wms",
        coverage_id="Agricultural_Land_Classification_Provisional_England",
        variable=Variable(name="agri_land_class", units="class", data_type=DataType.CATEGORICAL,
                          description="Agricultural land quality grades 1-5 — soil productivity"),
        resolution_m=50, category="soil",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3",
        citation="Natural England, Agricultural Land Classification",
        use_wms=True,
    )


@register("uk_flood_defences")
class UKFloodDefencesConnector(NationalWCSConnector):
    slug = "uk_flood_defences"
    display_name = "UK Flood Defences (EA)"
    base_url = "https://environment.data.gov.uk/spatialdata/spatial-flood-defences-including-standardised-attributes/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_flood_defences",
        display_name="UK Spatial Flood Defences (EA)",
        wcs_url="https://environment.data.gov.uk/spatialdata/spatial-flood-defences-including-standardised-attributes/wms",
        coverage_id="Spatial_Flood_Defences_Including_Standardised_Attributes",
        variable=Variable(name="flood_defence", units="class", data_type=DataType.CATEGORICAL,
                          description="Flood defence infrastructure (type, condition, standard of protection)"),
        resolution_m=10, category="hydrology",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="OGL v3",
        citation="Environment Agency, Spatial Flood Defences",
        use_wms=True,
    )


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


# ═══════════════════════════════════════════════════════════════════════
#  SPAIN — INSPIRE land use
# ═══════════════════════════════════════════════════════════════════════

@register("spain_land_use")
class SpainLandUseConnector(NationalWCSConnector):
    slug = "spain_land_use"
    display_name = "Spain Land Use INSPIRE (IDEE)"
    base_url = "https://servicios.idee.es/wms-inspire/ocupacion-suelo"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="spain_land_use",
        display_name="Spain INSPIRE Land Use (IDEE)",
        wcs_url="https://servicios.idee.es/wms-inspire/ocupacion-suelo",
        coverage_id="LU.ExistingLandUse",
        variable=Variable(name="land_use", units="class", data_type=DataType.CATEGORICAL,
                          description="Spanish INSPIRE land use classification"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=-18.2, min_lat=27.6, max_lon=4.4, max_lat=43.8),
        license="CC-BY 4.0", citation="IGN/IDEE, INSPIRE Land Use",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NETHERLANDS — protected areas
# ═══════════════════════════════════════════════════════════════════════

@register("netherlands_natura2000")
class NetherlandsNatura2000Connector(NationalWCSConnector):
    slug = "netherlands_natura2000"
    display_name = "Netherlands Natura 2000 (PDOK)"
    base_url = "https://service.pdok.nl/rvo/natura2000/wms/v1_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_natura2000",
        display_name="Netherlands Natura 2000 Protected Areas (PDOK)",
        wcs_url="https://service.pdok.nl/rvo/natura2000/wms/v1_0",
        coverage_id="natura2000",
        variable=Variable(name="protected_area", units="class", data_type=DataType.CATEGORICAL,
                          description="Natura 2000 protected areas — constraints on land use"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="RVO, Natura 2000 via PDOK",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  NORWAY — avalanche/landslide hazard (NVE)
# ═══════════════════════════════════════════════════════════════════════

# Disabled 2026-05-31: NVE's SnoskredAktsomhet is a dynamic ArcGIS MapServer that
# only renders the hazard polygons as RGBA cartographic *symbology* (export over
# Romsdalen returns a 4-band uint8 image of colour bytes, not class codes). There
# is no value-raster / WCS coverage, so zonal extraction would return meaningless
# RGB bytes — same unservable pattern as taiwan_dem / indonesia_lc. Re-enable only
# if NVE publishes a coded value raster or a queryable coverage.
@register("norway_avalanche")
class NorwayAvalancheConnector(NationalWCSConnector):
    slug = "norway_avalanche"
    display_name = "Norway Snow Avalanche Zones (NVE)"
    base_url = "https://gis3.nve.no/map/rest/services/SnoskredAktsomhet/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_avalanche",
        display_name="Norway Snow Avalanche Susceptibility (NVE)",
        wcs_url="https://gis3.nve.no/map/rest/services/SnoskredAktsomhet/MapServer/WMSServer",
        coverage_id="0,1,2,3",
        variable=Variable(name="avalanche_risk", units="class", data_type=DataType.CATEGORICAL,
                          description="Snow avalanche susceptibility zones (1: Caution area)"),
        resolution_m=25, category="cryosphere",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NVE, Snøskred Aktsomhet",
        # Avalanche zones exist only in steep terrain; the Oslo land anchor is flat.
        # Pin the Loen area (Nordfjord) which has extensive mapping.
        health_anchor=(6.85, 61.87),
        # Map RGBA symbology back to class values.
        color_map={
            (255, 127, 127): 1,  # Aktsomhetsområde (Light red/Pink)
            (220, 50, 32): 1,    # S3 Aktsomhetsområde (Red)
            (204, 121, 167): 1,  # S2 Aktsomhetsområde (Magenta)
        },
    )


# Disabled 2026-05-31: NVE's Skredfaresoner3 is a dynamic ArcGIS MapServer serving
# the hazard zones as RGBA symbology only (export returns a 4-band uint8 colour
# image — uniform/transparent outside mapped zones), not a coded value raster. No
# WCS coverage exists, so zonal extraction can't recover class values. Same
# unservable pattern as taiwan_dem / indonesia_lc. Re-enable only with a real
# value raster or queryable coverage.
@register("norway_landslide")
class NorwayLandslideConnector(NationalWCSConnector):
    slug = "norway_landslide"
    display_name = "Norway Landslide Hazard (NVE)"
    base_url = "https://gis3.nve.no/map/rest/services/Skredfaresoner3/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_landslide",
        display_name="Norway Landslide Hazard Zones (NVE)",
        wcs_url="https://gis3.nve.no/map/rest/services/Skredfaresoner3/MapServer/WMSServer",
        coverage_id="5",
        variable=Variable(name="landslide_hazard", units="class", data_type=DataType.CATEGORICAL,
                          description="Landslide hazard zones (100: 1/100yr, 1000: 1/1000yr, 5000: 1/5000yr)"),
        resolution_m=25, category="geology",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NVE, Skredfaresoner",
        # Landslide hazard zones are mapped around steep-terrain settlements.
        # Pin the Loen area (Nordfjord) which has extensive mapping.
        health_anchor=(6.85, 61.87),
        # Map RGBA symbology back to class values.
        color_map={
            (168, 0, 0): 100,    # 1/100 year
            (255, 85, 0): 1000,   # 1/1000 year
            (255, 170, 0): 5000,  # 1/5000 year
            (169, 0, 230): 1,     # Influence area (Pavirkningsomrade)
            (230, 0, 169): 1,     # Influence area (Pink variant)
            (203, 0, 149): 1,     # Influence area (Pink variant)
            (217, 0, 187): 1,     # Influence area (Pink variant)
            (125, 0, 171): 1,     # Influence area (Purple variant)
            (149, 0, 203): 1,     # Influence area (Purple variant)
        },
    )


# ═══════════════════════════════════════════════════════════════════════
#  SCOTLAND DEM
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  EASTERN EUROPE DEMs
# ═══════════════════════════════════════════════════════════════════════

@register("croatia_dem")
class CroatiaDEMConnector(NationalWCSConnector):
    slug = "croatia_dem"
    display_name = "Croatia DEM 25m"
    base_url = "https://geoportal.dgu.hr/services/inspire/el/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="croatia_dem", display_name="Croatia DEM 25m (DGU INSPIRE)",
        wcs_url="https://geoportal.dgu.hr/services/inspire/el/wcs",
        coverage_id="EL.GridCoverage", variable=ELEV_VAR, resolution_m=25,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=13.5, min_lat=42.4, max_lon=19.5, max_lat=46.6),
        license="Open (DGU)", citation="DGU, Digitalni model reljefa",
        use_wms=True,
    )


# Disabled: GetCoverage backend is dead (the public proxy forwards to an
# unreachable localhost:8085 GeoServer → HTTP 404), and the only advertised
# coverage is a single ~10 km tile, not national. Verified 2026-05-30.
# @register("hungary_dem")
class HungaryDEMConnector(NationalWCSConnector):
    slug = "hungary_dem"
    display_name = "Hungary DEM 5m"
    base_url = "https://inspire.lechnerkozpont.hu/geoserver/EL.DEM/ows"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="hungary_dem", display_name="Hungary DDM 5m (Lechner INSPIRE)",
        wcs_url="https://inspire.lechnerkozpont.hu/geoserver/EL.DEM/ows",
        coverage_id="EL.GridCoverage", variable=ELEV_VAR, resolution_m=5,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=16.1, min_lat=45.7, max_lon=22.9, max_lat=48.6),
        license="Open (Hungary)", citation="Lechner Tudásközpont, Digitális Domborzatmodell",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  AFRICA
# ═══════════════════════════════════════════════════════════════════════

@register("dea_africa_fc")
class DEAAfricaFCConnector(NationalWCSConnector):
    slug = "dea_africa_fc"
    display_name = "DEA Africa Fractional Cover"
    base_url = "https://ows.digitalearth.africa"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_fc",
        display_name="Digital Earth Africa Fractional Cover 30m",
        wcs_url="https://ows.digitalearth.africa/wcs",
        coverage_id="fc_ls_summary_annual", protocol_version="2.0.1",
        variable=Variable(name="fractional_cover", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Vegetation fractional cover (bare, green, non-green)"),
        resolution_m=30, category="vegetation",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0",
        citation="Digital Earth Africa, Fractional Cover (Landsat)",
        crs="EPSG:6933", default_time="2022-01-01",
    )


@register("dea_africa_water")
class DEAAfricaWaterConnector(NationalWCSConnector):
    slug = "dea_africa_water"
    display_name = "DEA Africa Water Observations"
    base_url = "https://ows.digitalearth.africa"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="dea_africa_water",
        display_name="Digital Earth Africa Water Observations (WOfS)",
        wcs_url="https://ows.digitalearth.africa/wcs",
        coverage_id="wofs_ls_summary_alltime", protocol_version="2.0.1",
        # This coverage is 3-band (count_wet, count_clear, frequency); band 1 is
        # a wet-observation COUNT (~hundreds), not a frequency. Select the
        # `frequency` band so we surface the 0-1 water frequency, matching the
        # sibling australia_water_obs rather than an out-of-range count.
        extra_params={"rangesubset": "frequency"},
        variable=Variable(name="water_frequency", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="All-time water observation frequency (Landsat)"),
        resolution_m=30, category="hydrology",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0",
        citation="Digital Earth Africa, Water Observations from Space",
        # Native grid of wofs_ls_summary_alltime is EPSG:6933 (verified via
        # DescribeCoverage, axisLabels "x y"). EPSG:3857 subset coords were read
        # by the server as 6933 metres, sampling dry land ~490 km off-target →
        # 0 valid water pixels. Matches sibling dea_africa_dem/lc (also 6933).
        crs="EPSG:6933",
        # Water frequency is 0/nodata over dry land. Pin permanent open water
        # (Lake Victoria) so the check samples a place with real observations.
        health_anchor=(33.0, -1.5),
    )


# ═══════════════════════════════════════════════════════════════════════
#  EASTERN EUROPE — soil & land cover
# ═══════════════════════════════════════════════════════════════════════

# Disabled: mapy.geoportal.gov.pl refuses TCP from non-PL IPs (see poland_nmt note).
# @register("poland_soil")
class PolandSoilConnector(NationalWCSConnector):
    slug = "poland_soil"
    display_name = "Poland Soil Map (PIG)"
    base_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/SoilMap"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="poland_soil",
        display_name="Poland Soil Map 1:500k (PIG-PIB)",
        wcs_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/SoilMap",
        coverage_id="SoilMap",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Polish soil classification 1:500,000"),
        resolution_m=500, category="soil",
        bbox=BoundingBox(min_lon=14.1, min_lat=49.0, max_lon=24.2, max_lat=54.9),
        license="Open (Poland)", citation="PIG-PIB, Mapa Gleb Polski",
    )


# ═══════════════════════════════════════════════════════════════════════
#  BALKANS & SOUTHEASTERN EUROPE
# ═══════════════════════════════════════════════════════════════════════

@register("greece_geology")
class GreeceGeologyConnector(NationalWCSConnector):
    slug = "greece_geology"
    display_name = "Greece Geological Map (IGME)"
    base_url = "http://gaia.igme.gr:8080/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="greece_geology",
        display_name="Greece Geological Map 1:500k (IGME/HSGME)",
        wcs_url="http://gaia.igme.gr:8080/geoserver/wms",
        coverage_id="Geochemistry:Arnea",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Greek geological units 1:500,000"),
        resolution_m=500, category="geology",
        bbox=BoundingBox(min_lon=19.4, min_lat=34.8, max_lon=29.6, max_lat=41.8),
        license="Open (IGME)", citation="IGME/HSGME, Geological Map of Greece",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  ICELAND — additional layers
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════

@register("portugal_geology")
class PortugalGeologyConnector(NationalWCSConnector):
    slug = "portugal_geology"
    display_name = "Portugal Geological Map (LNEG)"
    base_url = "https://sig.lneg.pt/server/services/CGP1M/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="portugal_geology",
        display_name="Portugal Geological Map 1:1M (LNEG)",
        wcs_url="https://sig.lneg.pt/server/services/CGP1M/MapServer/WMSServer",
        coverage_id="Unidades_Geológicas_da_Plataforma183",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Portuguese geological units 1:1,000,000"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-9.5, min_lat=36.9, max_lon=-6.2, max_lat=42.2),
        license="Open (LNEG)", citation="LNEG, Carta Geológica de Portugal",
        use_wms=True,
    )


@register("slovenia_soil")
class SloveniaSoilConnector(NationalWCSConnector):
    slug = "slovenia_soil"
    display_name = "Slovenia Soil Map (MKGP)"
    base_url = "https://rkg.gov.si/GERK/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="slovenia_soil",
        display_name="Slovenia Soil Map (MKGP/ARSO)",
        wcs_url="https://rkg.gov.si/GERK/wms",
        coverage_id="talnirazredi",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Slovenian soil classification"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=13.4, min_lat=45.4, max_lon=16.6, max_lat=46.9),
        license="Open (MKGP)", citation="MKGP, Talni razredi",
        use_wms=True,
    )


@register("slovenia_lc")
class SloveniaLCConnector(NationalWCSConnector):
    slug = "slovenia_lc"
    display_name = "Slovenia Land Use (MKGP)"
    base_url = "https://rkg.gov.si/GERK/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="slovenia_lc",
        display_name="Slovenia GERK Land Use (MKGP)",
        wcs_url="https://rkg.gov.si/GERK/wms",
        coverage_id="GERK_VRS",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Slovenian agricultural land use register"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=13.4, min_lat=45.4, max_lon=16.6, max_lat=46.9),
        license="Open (MKGP)", citation="MKGP, GERK Register",
        use_wms=True,
    )


@register("croatia_lc")
class CroatiaLCConnector(NationalWCSConnector):
    slug = "croatia_lc"
    display_name = "Croatia CORINE Land Cover"
    base_url = "https://geoportal.dgu.hr/services/inspire/lc/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="croatia_lc",
        display_name="Croatia CORINE Land Cover (DGU INSPIRE)",
        wcs_url="https://geoportal.dgu.hr/services/inspire/lc/wms",
        coverage_id="LC.LandCoverSurfaces",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Croatian land cover (CORINE nomenclature)"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=13.5, min_lat=42.4, max_lon=19.5, max_lat=46.6),
        license="Open (DGU)", citation="DGU, Croatia INSPIRE Land Cover",
        use_wms=True,
    )


@register("lithuania_soil")
class LithuaniaSoilConnector(NationalWCSConnector):
    slug = "lithuania_soil"
    display_name = "Lithuania Soil Map"
    base_url = "https://www.inspire-geoportal.lt/geoserver/so/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="lithuania_soil",
        display_name="Lithuania Soil Map (GIS-Centras INSPIRE)",
        wcs_url="https://www.inspire-geoportal.lt/geoserver/so/wms",
        coverage_id="SO.SoilBody",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Lithuanian soil classification"),
        resolution_m=100, category="soil",
        bbox=BoundingBox(min_lon=20.9, min_lat=53.9, max_lon=26.8, max_lat=56.5),
        license="Open (Lithuania)", citation="GIS-Centras, Lithuania INSPIRE Soil",
        use_wms=True,
    )

