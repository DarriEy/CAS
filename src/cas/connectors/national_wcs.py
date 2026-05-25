# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""National WCS/WMS DEM and soil connectors — factory pattern for countries with OGC services.

Each country is a thin registration on top of a shared WCS extraction flow.
This avoids duplicating the same WCS GetCoverage / WMS GetMap logic across 20+ files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import structlog

from cas.connectors.base import BaseConnector
from cas.core.exceptions import DataFormatError
from cas.core.models import (
    AggregationMethod,
    AttributeResult,
    BoundingBox,
    Dataset,
    DataType,
    Geometry,
    Protocol,
    QualityFlag,
    TemporalExtent,
    TemporalType,
    TimeRange,
    Variable,
)
from cas.core.registry import register
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()


@dataclass
class NationalDatasetConfig:
    slug: str
    display_name: str
    wcs_url: str
    coverage_id: str
    variable: Variable
    resolution_m: float
    bbox: BoundingBox
    protocol_version: str = "1.0.0"
    crs: str = "EPSG:4326"
    output_format: str = "GeoTIFF"
    extra_params: dict = field(default_factory=dict)
    license: str = "Open"
    citation: str = ""
    category: str = "elevation"
    auth_token_env: str = ""
    nodata_value: float | None = None


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Point":
        lon, lat = geometry.coordinates[0], geometry.coordinates[1]
        buf = 0.001
        return (lon - buf, lat - buf, lon + buf, lat + buf)
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


class NationalWCSConnector(BaseConnector):
    """Base class for national WCS-based connectors."""

    _config: NationalDatasetConfig

    async def list_datasets(self) -> list[Dataset]:
        cfg = self._config
        return [
            Dataset(
                id=f"{cfg.slug}:{cfg.variable.name}",
                provider=cfg.slug,
                name=f"{cfg.display_name}",
                description=(
                    f"{cfg.display_name} ({cfg.resolution_m}m)"
                    f" — {cfg.variable.description or cfg.variable.name}"
                ),
                variables=[cfg.variable],
                resolution_m=cfg.resolution_m,
                crs=cfg.crs,
                bbox=cfg.bbox,
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.WCS,
                license=cfg.license,
                citation=cfg.citation,
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        cfg = self._config
        bbox = _geometry_to_bbox(geometry)

        import os

        headers: dict[str, str] = {}
        params: dict = {}

        if cfg.protocol_version == "2.0.1":
            params = {
                "service": "WCS",
                "version": "2.0.1",
                "request": "GetCoverage",
                "CoverageId": cfg.coverage_id,
                "format": "image/tiff",
                "subset": [
                    f"Long({bbox[0]},{bbox[2]})",
                    f"Lat({bbox[1]},{bbox[3]})",
                ],
                **cfg.extra_params,
            }
        else:
            params = {
                "service": "WCS",
                "version": "1.0.0",
                "request": "GetCoverage",
                "coverage": cfg.coverage_id,
                "CRS": cfg.crs,
                "BBOX": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}",
                "width": "500",
                "height": "500",
                "format": cfg.output_format,
                **cfg.extra_params,
            }

        if cfg.auth_token_env:
            token = os.environ.get(cfg.auth_token_env, "")
            if token:
                params["token"] = token

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                if resp.status_code != 200:
                    raise DataFormatError(cfg.slug, f"WCS returned {resp.status_code}")
                content_type = resp.headers.get("content-type", "")
                if "xml" in content_type and "tiff" not in content_type:
                    raise DataFormatError(cfg.slug, f"WCS error: {resp.text[:300]}")
                raster_bytes = resp.content
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(cfg.slug, f"WCS request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        if cfg.nodata_value is not None:
            nodata = cfg.nodata_value

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        is_categorical = cfg.variable.data_type == DataType.CATEGORICAL
        agg = AggregationMethod.DISTRIBUTION if is_categorical else AggregationMethod.MEAN
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=cfg.variable.data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=cfg.variable.name, value=value,
            units=cfg.variable.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=cfg.slug, elapsed_ms=elapsed_ms,
            provenance=f"WCS: {cfg.coverage_id}",
        )


# ═══════════════════════════════════════════════════════════════════════
#  NORDIC
# ═══════════════════════════════════════════════════════════════════════

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


@register("estonia_dem")
class EstoniaDEMConnector(NationalWCSConnector):
    slug = "estonia_dem"
    display_name = "Estonia DEM 1m"
    base_url = "https://kaart.maaamet.ee/wms/fotokaart"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="estonia_dem", display_name="Estonia DEM 1m (Maa-amet)",
        wcs_url="https://kaart.maaamet.ee/wms/fotokaart",
        coverage_id="dem_1m", variable=ELEV_VAR,
        resolution_m=1,
        bbox=BoundingBox(min_lon=21.8, min_lat=57.5, max_lon=28.2, max_lat=59.7),
        license="Estonian Open Data License", citation="Maa-amet, Estonian Land Board",
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
        coverage_id="lidar-composite-digital-terrain-model-dtm-1m",
        variable=ELEV_VAR, resolution_m=1, protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=-6.5, min_lat=49.8, max_lon=2.0, max_lat=55.9),
        license="Open Government Licence v3", citation="Environment Agency, LiDAR Composite DTM",
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


@register("italy_tinitaly")
class ItalyTINITALYConnector(NationalWCSConnector):
    slug = "italy_tinitaly"
    display_name = "Italy TINITALY 10m"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_tinitaly", display_name="Italy TINITALY/1.1 10m (INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1__tinitaly_dem",
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


@register("poland_nmt")
class PolandNMTConnector(NationalWCSConnector):
    slug = "poland_nmt"
    display_name = "Poland NMT 1m"
    base_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModel"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="poland_nmt", display_name="Poland NMT 1m (GUGiK)",
        wcs_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModel",
        coverage_id="DigitalTerrainModel",
        variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=14.1, min_lat=49.0, max_lon=24.2, max_lat=54.9),
        license="Open (Poland)", citation="GUGiK, Numeryczny Model Terenu",
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
        coverage_id="DMV", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=13.4, min_lat=45.4, max_lon=16.6, max_lat=46.9),
        license="CC-BY 4.0", citation="GURS, Digitalni Model Višin",
    )


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
        coverage_id="hrdem-lidar:dtm", variable=ELEV_VAR, resolution_m=2,
        bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
        license="Open Government Licence - Canada", citation="NRCan, High Resolution DEM",
    )


# ═══════════════════════════════════════════════════════════════════════
#  NATIONAL SOIL (WCS/WMS)
# ═══════════════════════════════════════════════════════════════════════

CLAY_VAR = Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100))

@register("netherlands_soil")
class NetherlandsSoilConnector(NationalWCSConnector):
    slug = "netherlands_soil"
    display_name = "Netherlands Soil Map (BRO)"
    base_url = "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_soil", display_name="Netherlands Bodemkaart 1:50k (BRO/PDOK)",
        wcs_url="https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0",
        coverage_id="bodemkaart",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Dutch soil classification 1:50,000"),
        resolution_m=50, category="soil",
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="BRO, Bodemkaart van Nederland 1:50.000",
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
        coverage_id="0",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Finnish quaternary deposits classification"),
        resolution_m=20, category="soil",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="GTK, Geological Survey of Finland",
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
        coverage_id="ar5_flate",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian land resource classification (104 classes)"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="CC-BY 4.0 (NLOD)", citation="NIBIO, AR5 Arealressurskart",
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
        coverage_id="ga_ls_landcover_class_cyear_3",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Annual land cover from Landsat (FAO LCCS v2, 1988-present)"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="DEA, Geoscience Australia Land Cover",
    )


# ═══════════════════════════════════════════════════════════════════════
#  AMERICAS — SOIL & LAND COVER
# ═══════════════════════════════════════════════════════════════════════

@register("brazil_soil")
class BrazilSoilConnector(NationalWCSConnector):
    slug = "brazil_soil"
    display_name = "Brazil Soil Map (EMBRAPA)"
    base_url = "https://geoinfo.dados.embrapa.br/geoserver/ows"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="brazil_soil", display_name="Brazil Soil Map (EMBRAPA GeoInfo)",
        wcs_url="https://geoinfo.dados.embrapa.br/geoserver/ows",
        coverage_id="geonode:soils_brazil_wrb_wgs84",
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
        coverage_id="1",
        variable=Variable(name="cropland", units="class", data_type=DataType.CATEGORICAL,
                          description="Canadian crop type classification (~70 classes, annual)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
        license="Open Government Licence - Canada", citation="AAFC, Annual Crop Inventory",
    )


@register("argentina_soil")
class ArgentinaSoilConnector(NationalWCSConnector):
    slug = "argentina_soil"
    display_name = "Argentina Soil Map (INTA)"
    base_url = "http://geointa.inta.gov.ar/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="argentina_soil", display_name="Argentina Soil Map 1:500k (INTA GeoINTA)",
        wcs_url="http://geointa.inta.gov.ar/geoserver/wms",
        coverage_id="suelos_argentina_1_500",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Argentine soil classification 1:500,000"),
        resolution_m=500, category="soil",
        bbox=BoundingBox(min_lon=-74, min_lat=-56, max_lon=-53, max_lat=-21),
        license="Open (INTA)", citation="INTA, GeoINTA Suelos",
    )


# ═══════════════════════════════════════════════════════════════════════
#  ASIA
# ═══════════════════════════════════════════════════════════════════════

@register("indonesia_dem")
class IndonesiaDEMConnector(NationalWCSConnector):
    slug = "indonesia_dem"
    display_name = "Indonesia DEMNAS 8m"
    base_url = "https://geoservices.big.go.id/raster/rest/services/DEMNAS/DEM_Indonesia/ImageServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="indonesia_dem", display_name="Indonesia DEMNAS 8m (BIG)",
        wcs_url="https://geoservices.big.go.id/raster/rest/services/DEMNAS/DEM_Indonesia/ImageServer/WCSServer",
        coverage_id="1", variable=ELEV_VAR, resolution_m=8,
        bbox=BoundingBox(min_lon=95, min_lat=-11, max_lon=141, max_lat=6),
        license="Open (BIG)", citation="Badan Informasi Geospasial, DEMNAS",
    )


@register("taiwan_dem")
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
        coverage_id="dgm200", variable=ELEV_VAR, resolution_m=200,
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="GeoNutzV (GeoBasis-DE / BKG)", citation="BKG, Digitales Geländemodell 200",
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
        coverage_id="nw_dgm", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=5.9, min_lat=50.3, max_lon=9.5, max_lat=52.6),
        license="DL-DE/Zero", citation="Geobasis NRW, Digitales Geländemodell 1m",
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
    display_name = "Germany LBM-DE Land Cover"
    base_url = "https://sgx.geodatenzentrum.de/wms_lbm_de"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="germany_lc", display_name="Germany LBM-DE Land Cover (BKG)",
        wcs_url="https://sgx.geodatenzentrum.de/wms_lbm_de",
        coverage_id="lbm_de_2021",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="German land cover model (CORINE nomenclature, 1ha MMU)"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=5.9, min_lat=47.3, max_lon=15.0, max_lat=55.1),
        license="GeoNutzV", citation="BKG, Landbedeckungsmodell Deutschland 2021",
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
    base_url = "https://agroenvgeo.data.inra.fr/geoserver/gissol_rmqs/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="france_soil", display_name="France Soil (GIS Sol / INRAE RMQS)",
        wcs_url="https://agroenvgeo.data.inra.fr/geoserver/gissol_rmqs/wms",
        coverage_id="gissol_rmqs:rmqs_topsoil",
        variable=Variable(name="soil_properties", units="various", data_type=DataType.CONTINUOUS,
                          description="French soil monitoring network (pH, SOC, trace elements)"),
        resolution_m=1000, category="soil",
        bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
        license="Open (INRAE)", citation="INRAE / GIS Sol, RMQS",
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
        coverage_id="NMD",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Swedish national land cover (10m, Sentinel-2 based)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=11.0, min_lat=55.3, max_lon=24.2, max_lat=69.1),
        license="Open (Sweden)", citation="Naturvårdsverket, Nationella Marktäckedata",
    )


@register("sweden_soil")
class SwedenSoilConnector(NationalWCSConnector):
    slug = "sweden_soil"
    display_name = "Sweden Soil Map (SGU)"
    base_url = "https://resource.sgu.se/service/wms/130/jordarter-25-100-tusen"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="sweden_soil", display_name="Sweden Quaternary Deposits (SGU)",
        wcs_url="https://resource.sgu.se/service/wms/130/jordarter-25-100-tusen",
        coverage_id="jordarter",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Swedish quaternary deposits (1:25k-1:100k)"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=11.0, min_lat=55.3, max_lon=24.2, max_lat=69.1),
        license="Open (SGU)", citation="SGU, Geological Survey of Sweden",
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
        coverage_id="DHMVII_DTM_1m", variable=ELEV_VAR, resolution_m=1,
        bbox=BoundingBox(min_lon=2.5, min_lat=50.7, max_lon=5.9, max_lat=51.5),
        license="Gratis Open Data Vlaanderen",
        citation="Digitaal Hoogtemodel Vlaanderen II",
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
        coverage_id="COS2018_v2",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Portuguese land use/cover (CORINE-compatible)"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=-9.5, min_lat=36.9, max_lon=-6.2, max_lat=42.2),
        license="Open (DGT)", citation="DGT, Carta de Uso e Ocupação do Solo",
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
        coverage_id="dem_srtm_deriv", variable=ELEV_VAR, resolution_m=30,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0",
        citation="Digital Earth Africa, SRTM-derived slope/MRVBF",
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
        coverage_id="Edafologia",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Mexican soil classification (WRB)"),
        resolution_m=250, category="soil",
        bbox=BoundingBox(min_lon=-118, min_lat=14, max_lon=-86, max_lat=33),
        license="Open (INEGI)", citation="INEGI, Carta Edafológica",
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  LITHUANIA
# ═══════════════════════════════════════════════════════════════════════

@register("lithuania_dem")
class LithuaniaDEMConnector(NationalWCSConnector):
    slug = "lithuania_dem"
    display_name = "Lithuania DEM (INSPIRE)"
    base_url = "https://www.geoportal.lt/inspire-services/rest/services/INSPIRE/Elevation/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="lithuania_dem", display_name="Lithuania Elevation (GIS-Centras)",
        wcs_url="https://www.geoportal.lt/inspire-services/rest/services/INSPIRE/Elevation/MapServer/WMSServer",
        coverage_id="0", variable=ELEV_VAR, resolution_m=10,
        bbox=BoundingBox(min_lon=20.9, min_lat=53.9, max_lon=26.8, max_lat=56.5),
        license="Open (Lithuania)", citation="GIS-Centras, Lithuania INSPIRE Elevation",
    )


# ═══════════════════════════════════════════════════════════════════════
#  GEBCO BATHYMETRY
# ═══════════════════════════════════════════════════════════════════════

@register("gebco")
class GEBCOConnector(NationalWCSConnector):
    slug = "gebco"
    display_name = "GEBCO Bathymetry 500m"
    base_url = "https://wms.gebco.net/mapserv"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="gebco", display_name="GEBCO Global Bathymetry 500m",
        wcs_url="https://wms.gebco.net/mapserv",
        coverage_id="gebco_latest",
        variable=Variable(name="bathymetry", units="m", data_type=DataType.CONTINUOUS,
                          valid_range=(-11000, 9000),
                          description="Ocean/land elevation (bathymetry + topography)"),
        resolution_m=500, category="elevation",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open (GEBCO)",
        citation="GEBCO Compilation Group 2024, GEBCO 2024 Grid",
    )


# ═══════════════════════════════════════════════════════════════════════
#  GLOBAL PERMAFROST
# ═══════════════════════════════════════════════════════════════════════

@register("permafrost")
class PermafrostConnector(NationalWCSConnector):
    slug = "permafrost"
    display_name = "Global Permafrost (Zurich)"
    base_url = "https://geoserver.geo.uzh.ch/cryogis/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="permafrost",
        display_name="Global Permafrost Zonation Index 1km (U Zurich)",
        wcs_url="https://geoserver.geo.uzh.ch/cryogis/wms",
        coverage_id="PermafrostZonationIndex",
        variable=Variable(name="permafrost_index", units="index", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 1),
                          description="Permafrost zonation probability index"),
        resolution_m=1000, category="cryosphere",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open", citation="Obu et al. 2019, Global Permafrost Zonation Index",
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
        coverage_id="HabMoS_Scotland",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="EUNIS habitat classification (28 classes, AI/Sentinel-2)"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=-8.6, min_lat=54.6, max_lon=-0.7, max_lat=60.9),
        license="OGL v3", citation="NatureScot / Space Intelligence, Scotland Land Cover Map",
    )


# ═══════════════════════════════════════════════════════════════════════
#  DENMARK SOIL
# ═══════════════════════════════════════════════════════════════════════

@register("denmark_soil")
class DenmarkSoilConnector(NationalWCSConnector):
    slug = "denmark_soil"
    display_name = "Denmark Soil Map (GEUS)"
    base_url = "https://data.geus.dk/geusmap/ows/25832.jsp"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="denmark_soil",
        display_name="Denmark Jordartskort 1:200k (GEUS)",
        wcs_url="https://data.geus.dk/geusmap/ows/25832.jsp",
        coverage_id="insp_jordartskort_200000",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Danish quaternary deposits / soil type classification"),
        resolution_m=200, category="soil",
        bbox=BoundingBox(min_lon=8.0, min_lat=54.5, max_lon=15.2, max_lat=57.8),
        license="Open (GEUS)", citation="GEUS, Jordartskort over Danmark 1:200.000",
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  NORWAY SOIL
# ═══════════════════════════════════════════════════════════════════════

@register("norway_soil")
class NorwaySoilConnector(NationalWCSConnector):
    slug = "norway_soil"
    display_name = "Norway Soil Map (NIBIO)"
    base_url = "https://wms.nibio.no/cgi-bin/jordkart"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_soil",
        display_name="Norway Soil Map 1:5k-1:250k (NIBIO)",
        wcs_url="https://wms.nibio.no/cgi-bin/jordkart",
        coverage_id="jordkart",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian soil type classification"),
        resolution_m=5, category="soil",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NIBIO, Jordkart (Soil Map of Norway)",
    )


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
        coverage_id="Vegetasjonstypar",
        variable=Variable(name="vegetation_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian vegetation type classification"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NIBIO, Vegetasjonskartet",
    )


# ═══════════════════════════════════════════════════════════════════════
#  GERMANY — additional state DEMs
# ═══════════════════════════════════════════════════════════════════════

@register("germany_sachsen_anhalt_dem")
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  CHILE — soil
# ═══════════════════════════════════════════════════════════════════════

@register("chile_soil")
class ChileSoilConnector(NationalWCSConnector):
    slug = "chile_soil"
    display_name = "Chile Soil Map (CIREN)"
    base_url = "https://esri.ciren.cl/server/rest/services/IDEMINAGRI/SUELOS_AGROLOGICOS/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="chile_soil",
        display_name="Chile Agrological Soil Map (CIREN/MINAGRI)",
        wcs_url="https://esri.ciren.cl/server/rest/services/IDEMINAGRI/SUELOS_AGROLOGICOS/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Chilean agrological soil classification"),
        resolution_m=100, category="soil",
        bbox=BoundingBox(min_lon=-76, min_lat=-56, max_lon=-66, max_lat=-17),
        license="Open (CIREN)", citation="CIREN / MINAGRI, Suelos Agrológicos de Chile",
    )


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
        coverage_id="0",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Peruvian national vegetation/land cover classification (60 classes)"),
        resolution_m=250, category="land_cover",
        bbox=BoundingBox(min_lon=-82, min_lat=-19, max_lon=-68, max_lat=1),
        license="Open (MINAM)", citation="MINAM, Mapa Nacional de Cobertura Vegetal",
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
        protocol_version="2.0.1",
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  MEXICO — additional (CONABIO land use)
# ═══════════════════════════════════════════════════════════════════════

@register("mexico_conabio_lc")
class MexicoCONABIOLCConnector(NationalWCSConnector):
    slug = "mexico_conabio_lc"
    display_name = "Mexico CONABIO Land Use"
    base_url = "http://geoportal.conabio.gob.mx/cgi-bin/mapserv"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="mexico_conabio_lc",
        display_name="Mexico CONABIO Uso de Suelo y Vegetación",
        wcs_url="http://geoportal.conabio.gob.mx/cgi-bin/mapserv",
        coverage_id="uso_suelo_vegetacion",
        extra_params={"map": "/web/map_files/cws/uso_suelo_map.map"},
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Mexican land use/vegetation (CONABIO/INEGI)"),
        resolution_m=250, category="land_cover",
        bbox=BoundingBox(min_lon=-118, min_lat=14, max_lon=-86, max_lat=33),
        license="Open (CONABIO)", citation="CONABIO / INEGI, Uso de Suelo y Vegetación",
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
        coverage_id="LC.LandCoverRaster.2018",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Finnish CORINE land cover 2018 (20m raster)"),
        resolution_m=20, category="land_cover",
        bbox=BoundingBox(min_lon=19.1, min_lat=59.5, max_lon=31.6, max_lat=70.1),
        license="CC-BY 4.0", citation="SYKE, CORINE Land Cover Finland 2018",
    )


# ═══════════════════════════════════════════════════════════════════════
#  DENMARK — geology/groundwater
# ═══════════════════════════════════════════════════════════════════════

@register("denmark_geology")
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


@register("denmark_nitrate")
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


@register("denmark_water_supply")
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
        coverage_id="pohi_vr",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Estonian base map land cover classification"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=21.8, min_lat=57.5, max_lon=28.2, max_lat=59.7),
        license="Estonian Open Data",
        citation="Maa-amet, Estonian Land Board Base Map",
    )


# ═══════════════════════════════════════════════════════════════════════
#  ARGENTINA — land cover (MapBiomas via WMS)
# ═══════════════════════════════════════════════════════════════════════

@register("argentina_lc")
class ArgentinaLCConnector(NationalWCSConnector):
    slug = "argentina_lc"
    display_name = "Argentina Land Cover (MapBiomas)"
    base_url = "https://storage.googleapis.com/mapbiomas-public/initiatives/argentina/collection_2/coverage"
    protocol = "rest"
    _config = NationalDatasetConfig(
        slug="argentina_lc",
        display_name="Argentina MapBiomas Land Cover 30m (1998-2024)",
        wcs_url="https://storage.googleapis.com/mapbiomas-public/initiatives/argentina/collection_2/coverage",
        coverage_id="mapbiomas_argentina",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="MapBiomas Argentina annual land cover (15 classes, Landsat)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-74, min_lat=-56, max_lon=-53, max_lat=-21),
        license="CC-BY-SA 4.0",
        citation="MapBiomas Argentina, Collection 2",
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
        coverage_id="Losmasse_Norge",
        variable=Variable(name="surficial_geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Norwegian surficial deposits (till, marine clay, peat, etc.)"),
        resolution_m=50, category="geology",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="CC-BY 4.0 (NLOD)", citation="NGU, Løsmassekart (Surficial Geology Map of Norway)",
    )


# ═══════════════════════════════════════════════════════════════════════
#  NETHERLANDS — land cover
# ═══════════════════════════════════════════════════════════════════════

@register("netherlands_lc")
class NetherlandsLCConnector(NationalWCSConnector):
    slug = "netherlands_lc"
    display_name = "Netherlands BRT Land Cover"
    base_url = "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="netherlands_lc",
        display_name="Netherlands BRT/TOP10NL Land Cover (PDOK)",
        wcs_url="https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0",
        coverage_id="standaard",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Dutch topographic land cover from BRT/TOP10NL"),
        resolution_m=10, category="land_cover",
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="Kadaster, BRT/TOP10NL via PDOK",
    )


# ═══════════════════════════════════════════════════════════════════════
#  ITALY — DEM slope/aspect derivatives
# ═══════════════════════════════════════════════════════════════════════

@register("italy_slope")
class ItalySlopeConnector(NationalWCSConnector):
    slug = "italy_slope"
    display_name = "Italy Slope (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_slope",
        display_name="Italy Slope 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1__tinitaly_slope",
        variable=Variable(name="slope", units="degrees", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 90),
                          description="Terrain slope — controls runoff velocity and erosion"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 slope",
    )


@register("italy_aspect")
class ItalyAspectConnector(NationalWCSConnector):
    slug = "italy_aspect"
    display_name = "Italy Aspect (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_aspect",
        display_name="Italy Aspect 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1__tinitaly_hsv",
        variable=Variable(name="aspect", units="degrees", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 360),
                          description="Terrain aspect — controls solar radiation and snowmelt"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 aspect",
    )


@register("italy_hillshade")
class ItalyHillshadeConnector(NationalWCSConnector):
    slug = "italy_hillshade"
    display_name = "Italy Hillshade (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_hillshade",
        display_name="Italy Hillshade 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1__tinitaly_hshd",
        variable=Variable(name="hillshade", units="index", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 255),
                          description="Terrain hillshade — visualizes topographic features"),
        resolution_m=10, category="elevation",
        bbox=BoundingBox(min_lon=6.6, min_lat=36.6, max_lon=18.6, max_lat=47.1),
        license="CC-BY 4.0", citation="Tarquini et al. 2023, TINITALY/1.1 hillshade",
    )


@register("italy_skyview")
class ItalySkyviewConnector(NationalWCSConnector):
    slug = "italy_skyview"
    display_name = "Italy Sky-View Factor (TINITALY)"
    base_url = "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="italy_skyview",
        display_name="Italy Sky-View Factor 10m (TINITALY/INGV)",
        wcs_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        coverage_id="TINItaly_1_1__tinitaly_hsv",
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

@register("france_geology")
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
    )


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
        variable=Variable(name="lithology", units="class", data_type=DataType.CATEGORICAL,
                          description="Simplified lithological classification — controls permeability"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
        license="Open (BRGM)", citation="BRGM, Lithologie Simplifiée",
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  UK — soil (Soilscapes/Cranfield via ArcGIS REST)
# ═══════════════════════════════════════════════════════════════════════

@register("uk_soilscapes")
class UKSoilscapesConnector(NationalWCSConnector):
    slug = "uk_soilscapes"
    display_name = "UK Soilscapes (Cranfield)"
    base_url = "https://www.landis.org.uk/arcgis/rest/services/UKSoilObservatory/Soilscapes_Cranfield/MapServer/WMSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="uk_soilscapes",
        display_name="UK Soilscapes Soil Map (Cranfield/LandIS)",
        wcs_url="https://www.landis.org.uk/arcgis/rest/services/UKSoilObservatory/Soilscapes_Cranfield/MapServer/WMSServer",
        coverage_id="0",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="UK soil landscape classification (27 types)"),
        resolution_m=100, category="soil",
        bbox=BoundingBox(min_lon=-8.6, min_lat=49.8, max_lon=2.0, max_lat=60.9),
        license="Open (LandIS)", citation="Cranfield University, Soilscapes",
    )


# ═══════════════════════════════════════════════════════════════════════
#  SWITZERLAND — soil erosion + organic soils
# ═══════════════════════════════════════════════════════════════════════

@register("swiss_soil_erosion")
class SwissSoilErosionConnector(NationalWCSConnector):
    slug = "swiss_soil_erosion"
    display_name = "Switzerland Soil Erosion Risk"
    base_url = "https://data.geo.admin.ch/api/stac/v1"
    protocol = "stac_cog"
    _config = NationalDatasetConfig(
        slug="swiss_soil_erosion",
        display_name="Switzerland Monthly Soil Erosion Risk (BAFU)",
        wcs_url="https://data.geo.admin.ch/api/stac/v1",
        coverage_id="ch.bafu.erosion-gruenland_bodenabtrag",
        variable=Variable(name="soil_erosion", units="t/ha/yr", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Soil erosion risk for grassland — controls sediment yield"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (swisstopo)", citation="BAFU, Soil Erosion Risk Maps",
    )


@register("swiss_organic_soils")
class SwissOrganicSoilsConnector(NationalWCSConnector):
    slug = "swiss_organic_soils"
    display_name = "Switzerland Organic Soils"
    base_url = "https://data.geo.admin.ch/api/stac/v1"
    protocol = "stac_cog"
    _config = NationalDatasetConfig(
        slug="swiss_organic_soils",
        display_name="Switzerland Organic Soils Distribution (Agroscope)",
        wcs_url="https://data.geo.admin.ch/api/stac/v1",
        coverage_id="ch.agroscope.abschaetzung-organische_boeden",
        variable=Variable(name="organic_soil", units="class", data_type=DataType.CATEGORICAL,
                          description="Distribution of organic soils — controls carbon/water storage"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
        license="Open (Agroscope)", citation="Agroscope, Organic Soils of Switzerland",
    )


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
    )


# ═══════════════════════════════════════════════════════════════════════
#  AUSTRALIA — additional DEA products (water, fractional cover)
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
        coverage_id="water_observations", protocol_version="2.0.1",
        variable=Variable(name="water_frequency", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Water presence frequency from Landsat — controls surface water dynamics"),
        resolution_m=25, category="hydrology",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="DEA, Water Observations from Space",
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
        coverage_id="ga_ls_fc_3", protocol_version="2.0.1",
        variable=Variable(name="fractional_cover", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Photosynthetic/non-photosynthetic veg and bare soil fractions"),
        resolution_m=25, category="land_cover",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0",
        citation="DEA, Fractional Cover (Landsat)",
    )


@register("australia_mangrove")
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  GLOBAL — Ramsar wetlands
# ═══════════════════════════════════════════════════════════════════════

@register("ramsar_wetlands")
class RamsarWetlandsConnector(NationalWCSConnector):
    slug = "ramsar_wetlands"
    display_name = "Ramsar Wetland Sites (Global)"
    base_url = "https://rsis.ramsar.org/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="ramsar_wetlands",
        display_name="Ramsar Convention Wetland Sites (Global)",
        wcs_url="https://rsis.ramsar.org/geoserver/wms",
        coverage_id="ramsar_sdi:Ramsar_centroids_published",
        variable=Variable(name="ramsar_site", units="class", data_type=DataType.CATEGORICAL,
                          description="Ramsar Convention designated wetland locations"),
        resolution_m=1000, category="hydrology",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open (Ramsar Convention)",
        citation="Ramsar Convention Secretariat, Ramsar Sites Information Service",
    )


# ═══════════════════════════════════════════════════════════════════════
#  UK — WFD catchments
# ═══════════════════════════════════════════════════════════════════════

@register("uk_wfd_catchments")
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
        coverage_id="brpgewaspercelen",
        variable=Variable(name="crop_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Dutch agricultural crop parcels — annual crop type mapping"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=3.3, min_lat=50.7, max_lon=7.3, max_lat=53.6),
        license="CC-0", citation="RVO, BRP Gewaspercelen via PDOK",
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
    )


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
    )


@register("usgs_geology")
class USGSGeologyConnector(NationalWCSConnector):
    slug = "usgs_geology"
    display_name = "USGS State Geologic Map (US)"
    base_url = "https://mrdata.usgs.gov/services/sgmc"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="usgs_geology",
        display_name="USGS State Geologic Map Compilation (SGMC)",
        wcs_url="https://mrdata.usgs.gov/services/sgmc",
        coverage_id="0",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="US state geologic map compilation — lithology and age"),
        resolution_m=250, category="geology",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, State Geologic Map Compilation (SGMC)",
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
        coverage_id="0",
        variable=Variable(name="karst", units="class", data_type=DataType.CATEGORICAL,
                          description="Karst and pseudokarst areas — controls subsurface drainage"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
        license="Public Domain", citation="USGS, Karst Map of the US",
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
        wcs_url="https://www.mrlc.gov/geoserver/mrlc_display/wms",
        coverage_id="NLCD_2001_2021_Land_Cover_Change_Index_L48",
        variable=Variable(name="lc_change_index", units="class", data_type=DataType.CATEGORICAL,
                          description="NLCD land cover change index 2001-2021 — identifies changed areas"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-130, min_lat=22, max_lon=-64, max_lat=52),
        license="Public Domain",
        citation="USGS MRLC, NLCD Land Cover Change Index 2001-2021",
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
    )


# ═══════════════════════════════════════════════════════════════════════
#  NORWAY — avalanche/landslide hazard (NVE)
# ═══════════════════════════════════════════════════════════════════════

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
        coverage_id="0",
        variable=Variable(name="avalanche_risk", units="class", data_type=DataType.CATEGORICAL,
                          description="Snow avalanche susceptibility zones"),
        resolution_m=25, category="cryosphere",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NVE, Snøskred Aktsomhet",
    )


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
        coverage_id="0",
        variable=Variable(name="landslide_hazard", units="class", data_type=DataType.CATEGORICAL,
                          description="Landslide hazard zones (rockfall, debris flow, clay slides)"),
        resolution_m=25, category="geology",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="NLOD (Norway)", citation="NVE, Skredfaresoner",
    )


# ═══════════════════════════════════════════════════════════════════════
#  PAN-EUROPEAN DEM
# ═══════════════════════════════════════════════════════════════════════

@register("eu_dem")
class EUDEMConnector(NationalWCSConnector):
    slug = "eu_dem"
    display_name = "EU-DEM v1.1 25m"
    base_url = "https://image.discomap.eea.europa.eu/arcgis/services/Elevation/EUElev_DEM_V11/MapServer/WCSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="eu_dem", display_name="EU-DEM v1.1 25m (EEA/Copernicus)",
        wcs_url="https://image.discomap.eea.europa.eu/arcgis/services/Elevation/EUElev_DEM_V11/MapServer/WCSServer",
        coverage_id="1", variable=ELEV_VAR, resolution_m=25,
        bbox=BoundingBox(min_lon=-34, min_lat=27, max_lon=45, max_lat=72),
        license="Open (EEA)", citation="EEA, EU-DEM v1.1 (Copernicus Land Monitoring Service)",
    )


# ═══════════════════════════════════════════════════════════════════════
#  SWEDEN DEM
# ═══════════════════════════════════════════════════════════════════════

@register("sweden_dem")
class SwedenDEMConnector(NationalWCSConnector):
    slug = "sweden_dem"
    display_name = "Sweden DEM 2m"
    base_url = "https://maps.lantmateriet.se/topowebb-wcs/v1"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="sweden_dem", display_name="Sweden Elevation Model 2m (Lantmäteriet)",
        wcs_url="https://maps.lantmateriet.se/topowebb-wcs/v1",
        coverage_id="dem2m", variable=ELEV_VAR, resolution_m=2, protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=11.0, min_lat=55.3, max_lon=24.2, max_lat=69.1),
        license="Open (Lantmäteriet)", citation="Lantmäteriet, GSD-Höjddata grid 2+",
        auth_token_env="CAS_LANTMATERIET_API_KEY",
    )


# ═══════════════════════════════════════════════════════════════════════
#  SCOTLAND DEM
# ═══════════════════════════════════════════════════════════════════════

@register("scotland_dem")
class ScotlandDEMConnector(NationalWCSConnector):
    slug = "scotland_dem"
    display_name = "Scotland LiDAR DTM 1m"
    base_url = "https://remotesensingdata.gov.scot/services/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="scotland_dem", display_name="Scotland LiDAR DTM 1m (SG)",
        wcs_url="https://remotesensingdata.gov.scot/services/wcs",
        coverage_id="Scotland_DTM_1m", variable=ELEV_VAR, resolution_m=1,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=-7.7, min_lat=54.6, max_lon=-0.7, max_lat=60.9),
        license="Open Government Licence v3",
        citation="Scottish Government, National LiDAR DTM",
    )


# ═══════════════════════════════════════════════════════════════════════
#  EASTERN EUROPE DEMs
# ═══════════════════════════════════════════════════════════════════════

@register("latvia_dem")
class LatviaDEMConnector(NationalWCSConnector):
    slug = "latvia_dem"
    display_name = "Latvia DEM 1m"
    base_url = "https://lvmgeoserver.lvm.lv/geoserver/public/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="latvia_dem", display_name="Latvia LiDAR DEM 1m (LVM GEO)",
        wcs_url="https://lvmgeoserver.lvm.lv/geoserver/public/wcs",
        coverage_id="public:dem_1m", variable=ELEV_VAR, resolution_m=1,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=20.9, min_lat=55.7, max_lon=28.2, max_lat=58.1),
        license="Open (Latvia)", citation="LVM GEO, Latvia LiDAR DEM",
    )


@register("slovakia_dem")
class SlovakiaDEMConnector(NationalWCSConnector):
    slug = "slovakia_dem"
    display_name = "Slovakia DTM 5m"
    base_url = "https://zbgisws.skgeodesy.sk/inspire_wcs_el/service.svc/get"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="slovakia_dem", display_name="Slovakia DMR3.5 5m (UGKK)",
        wcs_url="https://zbgisws.skgeodesy.sk/inspire_wcs_el/service.svc/get",
        coverage_id="EL.GridCoverage.DTM", variable=ELEV_VAR, resolution_m=5,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=16.8, min_lat=47.7, max_lon=22.6, max_lat=49.6),
        license="Open (UGKK)", citation="UGKK, Digitálny model reliéfu",
    )


@register("croatia_dem")
class CroatiaDEMConnector(NationalWCSConnector):
    slug = "croatia_dem"
    display_name = "Croatia DEM 25m"
    base_url = "https://geoportal.dgu.hr/services/inspire/el_wcs/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="croatia_dem", display_name="Croatia DEM 25m (DGU INSPIRE)",
        wcs_url="https://geoportal.dgu.hr/services/inspire/el_wcs/wcs",
        coverage_id="EL.GridCoverage.DTM", variable=ELEV_VAR, resolution_m=25,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=13.5, min_lat=42.4, max_lon=19.5, max_lat=46.6),
        license="Open (DGU)", citation="DGU, Digitalni model reljefa",
    )


@register("hungary_dem")
class HungaryDEMConnector(NationalWCSConnector):
    slug = "hungary_dem"
    display_name = "Hungary DEM 5m"
    base_url = "https://inspire.fomi.hu/ows/el/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="hungary_dem", display_name="Hungary DDM 5m (Lechner INSPIRE)",
        wcs_url="https://inspire.fomi.hu/ows/el/wcs",
        coverage_id="EL.GridCoverage.DTM", variable=ELEV_VAR, resolution_m=5,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=16.1, min_lat=45.7, max_lon=22.9, max_lat=48.6),
        license="Open (Hungary)", citation="Lechner Tudásközpont, Digitális Domborzatmodell",
    )


@register("romania_dem")
class RomaniaDEMConnector(NationalWCSConnector):
    slug = "romania_dem"
    display_name = "Romania DEM 5m"
    base_url = "https://geoportal.ancpi.ro/inspire/el/wcs"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="romania_dem", display_name="Romania Elevation 5m (ANCPI INSPIRE)",
        wcs_url="https://geoportal.ancpi.ro/inspire/el/wcs",
        coverage_id="EL.GridCoverage.DTM", variable=ELEV_VAR, resolution_m=5,
        protocol_version="2.0.1",
        bbox=BoundingBox(min_lon=20.3, min_lat=43.6, max_lon=29.7, max_lat=48.3),
        license="Open (ANCPI)", citation="ANCPI, Modelul Digital al Terenului",
    )


# ═══════════════════════════════════════════════════════════════════════
#  GLOBAL TOPO+BATHYMETRY
# ═══════════════════════════════════════════════════════════════════════

BATHY_VAR = Variable(
    name="elevation", units="m", data_type=DataType.CONTINUOUS,
    valid_range=(-11000, 9000),
    description="Combined land elevation and ocean depth",
)


@register("etopo_2022")
class ETOPO2022Connector(NationalWCSConnector):
    slug = "etopo_2022"
    display_name = "ETOPO 2022 Global Topo+Bathy"
    base_url = "https://gis.ngdc.noaa.gov/arcgis/services/DEM_mosaics/DEM_global_mosaic/ImageServer/WCSServer"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="etopo_2022", display_name="ETOPO 2022 60s Global Relief (NOAA/NCEI)",
        wcs_url="https://gis.ngdc.noaa.gov/arcgis/services/DEM_mosaics/DEM_global_mosaic/ImageServer/WCSServer",
        coverage_id="1", variable=BATHY_VAR, resolution_m=1852,
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open (NOAA)", citation="NOAA NCEI, ETOPO 2022 Global Relief Model",
    )


# ═══════════════════════════════════════════════════════════════════════
#  SOUTH AMERICA
# ═══════════════════════════════════════════════════════════════════════

@register("brazil_lc")
class BrazilLCConnector(NationalWCSConnector):
    slug = "brazil_lc"
    display_name = "Brazil MapBiomas Land Cover 30m"
    base_url = "https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/collection_9"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="brazil_lc",
        display_name="Brazil MapBiomas Land Cover 30m",
        wcs_url="https://geoserver.mapbiomas.org/geoserver/ows",
        coverage_id="mapbiomas:brazil_coverage_2022",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Brazilian land cover/use (MapBiomas Collection 9)"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-74, min_lat=-34, max_lon=-34, max_lat=6),
        license="CC-BY-SA-4.0", citation="MapBiomas Project, Collection 9",
    )


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
        coverage_id="CGEO:COM_BIOMAS_250",
        variable=Variable(name="biome", units="class", data_type=DataType.CATEGORICAL,
                          description="Brazilian biomes (Amazon, Cerrado, Caatinga, etc.)"),
        resolution_m=250, category="land_cover",
        bbox=BoundingBox(min_lon=-74, min_lat=-34, max_lon=-34, max_lat=6),
        license="Open (IBGE)", citation="IBGE, Mapa de Biomas do Brasil",
    )


@register("brazil_geology")
class BrazilGeologyConnector(NationalWCSConnector):
    slug = "brazil_geology"
    display_name = "Brazil Geological Map (CPRM)"
    base_url = "https://geosgb.sgb.gov.br/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="brazil_geology",
        display_name="Brazil Geological Map 1:1M (CPRM/SGB)",
        wcs_url="https://geosgb.sgb.gov.br/geoserver/wms",
        coverage_id="geologia:geologia_1m",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="Brazilian geological units 1:1,000,000"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=-74, min_lat=-34, max_lon=-34, max_lat=6),
        license="Open (CPRM)", citation="CPRM/SGB, Mapa Geológico do Brasil",
    )


@register("brazil_hydrogeo")
class BrazilHydroGeoConnector(NationalWCSConnector):
    slug = "brazil_hydrogeo"
    display_name = "Brazil Hydrogeological Map (CPRM)"
    base_url = "https://geosgb.sgb.gov.br/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="brazil_hydrogeo",
        display_name="Brazil Hydrogeological Map (CPRM/SGB)",
        wcs_url="https://geosgb.sgb.gov.br/geoserver/wms",
        coverage_id="hidrogeologia:aquiferos",
        variable=Variable(name="aquifer_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Brazilian aquifer system classification"),
        resolution_m=1000, category="hydrology",
        bbox=BoundingBox(min_lon=-74, min_lat=-34, max_lon=-34, max_lat=6),
        license="Open (CPRM)", citation="CPRM/SGB, Mapa Hidrogeológico do Brasil",
    )


@register("chile_lc")
class ChileLCConnector(NationalWCSConnector):
    slug = "chile_lc"
    display_name = "Chile Land Cover (CONAF)"
    base_url = "https://ide.conaf.cl/geoserver/ows"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="chile_lc",
        display_name="Chile Catastro Bosque Nativo (CONAF)",
        wcs_url="https://ide.conaf.cl/geoserver/ows",
        coverage_id="catastro:bosque_nativo_2020",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Chilean native forest and land cover classification"),
        resolution_m=30, category="land_cover",
        bbox=BoundingBox(min_lon=-76, min_lat=-56, max_lon=-66, max_lat=-17),
        license="Open (CONAF)", citation="CONAF, Catastro de Bosque Nativo",
    )


# ═══════════════════════════════════════════════════════════════════════
#  AFRICA
# ═══════════════════════════════════════════════════════════════════════

@register("south_africa_lc")
class SouthAfricaLCConnector(NationalWCSConnector):
    slug = "south_africa_lc"
    display_name = "South Africa National Land Cover 20m"
    base_url = "https://gis.environment.gov.za/server/rest/services"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="south_africa_lc",
        display_name="South Africa National Land Cover 2020 (DFFE)",
        wcs_url="https://gis.environment.gov.za/server/services/SANLCChange/SA_NLC_2020/ImageServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="South African national land cover (72 classes)"),
        resolution_m=20, category="land_cover",
        bbox=BoundingBox(min_lon=16.4, min_lat=-34.9, max_lon=32.9, max_lat=-22.1),
        license="Open (DFFE)", citation="DFFE, South Africa National Land Cover 2020",
    )


@register("south_africa_geology")
class SouthAfricaGeologyConnector(NationalWCSConnector):
    slug = "south_africa_geology"
    display_name = "South Africa Geology (CGS)"
    base_url = "https://maps.geoscience.org.za/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="south_africa_geology",
        display_name="South Africa 1:1M Geology (Council for Geoscience)",
        wcs_url="https://maps.geoscience.org.za/geoserver/wms",
        coverage_id="cgs:geology_1m",
        variable=Variable(name="geology", units="class", data_type=DataType.CATEGORICAL,
                          description="South African geological units"),
        resolution_m=1000, category="geology",
        bbox=BoundingBox(min_lon=16.4, min_lat=-34.9, max_lon=32.9, max_lat=-22.1),
        license="Open (CGS)",
        citation="Council for Geoscience, Geological Map of South Africa",
    )


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
        coverage_id="fc_ls", protocol_version="2.0.1",
        variable=Variable(name="fractional_cover", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="Vegetation fractional cover (bare, green, non-green)"),
        resolution_m=30, category="vegetation",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0",
        citation="Digital Earth Africa, Fractional Cover (Landsat)",
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
        variable=Variable(name="water_frequency", units="%", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 100),
                          description="All-time water observation frequency (Landsat)"),
        resolution_m=30, category="hydrology",
        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
        license="CC-BY 4.0",
        citation="Digital Earth Africa, Water Observations from Space",
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
        coverage_id="india_soil_nbss",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Indian soil classification"),
        resolution_m=500, category="soil",
        bbox=BoundingBox(min_lon=68.1, min_lat=6.7, max_lon=97.4, max_lat=37.1),
        license="Open (ISRO/Bhuvan)", citation="NBSS&LUP / ISRO Bhuvan",
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
        coverage_id="india_lulc_50k",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Indian land use/land cover classification"),
        resolution_m=50, category="land_cover",
        bbox=BoundingBox(min_lon=68.1, min_lat=6.7, max_lon=97.4, max_lat=37.1),
        license="Open (ISRO/Bhuvan)", citation="NRSC/ISRO, LULC 1:50k",
    )


@register("japan_lc")
class JapanLCConnector(NationalWCSConnector):
    slug = "japan_lc"
    display_name = "Japan High-Res Land Use"
    base_url = "https://nlftp.mlit.go.jp/ksj/gml/data/L03-a/L03-a-2021/L03-a-2021.geojson"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="japan_lc",
        display_name="Japan Land Use 100m (MLIT NLNI)",
        wcs_url="https://disaportal.gsi.go.jp/server/rest/services/denshikokudonweb/wms",
        coverage_id="land_use_100m",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Japanese detailed land use classification"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=122.9, min_lat=24.0, max_lon=153.9, max_lat=45.6),
        license="Open (MLIT)", citation="MLIT, National Land Numerical Information",
    )


@register("south_korea_lc")
class SouthKoreaLCConnector(NationalWCSConnector):
    slug = "south_korea_lc"
    display_name = "South Korea Land Cover (ME)"
    base_url = "https://egis.me.go.kr/api/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="south_korea_lc",
        display_name="South Korea Land Cover (Ministry of Environment)",
        wcs_url="https://egis.me.go.kr/api/wms",
        coverage_id="landcover_lv2",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="South Korean land cover classification (level 2)"),
        resolution_m=5, category="land_cover",
        bbox=BoundingBox(min_lon=124.6, min_lat=33.1, max_lon=131.9, max_lat=38.6),
        license="Open (EGIS)",
        citation="Ministry of Environment, Korea Environmental Geographic Information",
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
        wcs_url="https://www.asris.csiro.au/arcgis/services/TERN/SLGA_Soil_Depth/MapServer/WCSServer",
        coverage_id="1",
        variable=Variable(name="soil_depth", units="m", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 20),
                          description="Estimated soil depth (SLGA)"),
        resolution_m=90, category="soil",
        bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
        license="CC-BY 4.0", citation="TERN/CSIRO, Soil and Landscape Grid of Australia",
    )


@register("nz_lc")
class NewZealandLCConnector(NationalWCSConnector):
    slug = "nz_lc"
    display_name = "New Zealand LCDB v5.0"
    base_url = "https://lris.scinfo.org.nz/services/wmts"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="nz_lc",
        display_name="New Zealand Land Cover Database v5.0 (LRIS)",
        wcs_url="https://data.lris.govt.nz/v1/wms/",
        coverage_id="lcdb-v50-land-cover-database-version-50",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="New Zealand land cover (33 classes, 2018/19)"),
        resolution_m=15, category="land_cover",
        bbox=BoundingBox(min_lon=166.0, min_lat=-47.5, max_lon=178.7, max_lat=-34.3),
        license="CC-BY 4.0", citation="LRIS/MfE, Land Cover Database v5.0",
        auth_token_env="CAS_LRIS_API_KEY",
    )


# ═══════════════════════════════════════════════════════════════════════
#  EASTERN EUROPE — soil & land cover
# ═══════════════════════════════════════════════════════════════════════

@register("poland_soil")
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


@register("romania_lc")
class RomaniaLCConnector(NationalWCSConnector):
    slug = "romania_lc"
    display_name = "Romania Land Cover (ANCPI)"
    base_url = "https://geoportal.ancpi.ro/inspire/lc/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="romania_lc",
        display_name="Romania CORINE Land Cover (ANCPI INSPIRE)",
        wcs_url="https://geoportal.ancpi.ro/inspire/lc/wms",
        coverage_id="LC.LandCoverSurface",
        variable=Variable(name="land_cover", units="class", data_type=DataType.CATEGORICAL,
                          description="Romanian land cover (CORINE nomenclature)"),
        resolution_m=100, category="land_cover",
        bbox=BoundingBox(min_lon=20.3, min_lat=43.6, max_lon=29.7, max_lat=48.3),
        license="Open (ANCPI)", citation="ANCPI, Romania INSPIRE Land Cover",
    )


@register("hungary_soil")
class HungarySoilConnector(NationalWCSConnector):
    slug = "hungary_soil"
    display_name = "Hungary Soil Map (NÉBIH)"
    base_url = "https://inspire.fomi.hu/ows/so/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="hungary_soil",
        display_name="Hungary Soil Map (NÉBIH/Lechner INSPIRE)",
        wcs_url="https://inspire.fomi.hu/ows/so/wms",
        coverage_id="SO.SoilBody",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="Hungarian soil classification (WRB)"),
        resolution_m=100, category="soil",
        bbox=BoundingBox(min_lon=16.1, min_lat=45.7, max_lon=22.9, max_lat=48.6),
        license="Open (Hungary)", citation="NÉBIH, Hungarian Soil Map",
    )
