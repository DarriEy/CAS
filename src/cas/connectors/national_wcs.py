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
from cas.extract.zonal import compute_zonal_stats, geometry_to_bbox, parse_geotiff, rasterize_geometry

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
    use_wms: bool = False




def _parse_wms_image(
    data: bytes,
    bbox: tuple[float, float, float, float],
    crs: str = "EPSG:4326",
):
    """Convert a WMS GetMap PNG/JPEG response to raster array + transform."""
    from io import BytesIO

    import numpy as np
    from PIL import Image
    from rasterio.transform import from_bounds

    img = Image.open(BytesIO(data))
    arr = np.array(img)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], arr.shape[1], arr.shape[0])
    return arr, transform, 0, crs


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
        bbox = geometry_to_bbox(geometry)

        import os

        headers: dict[str, str] = {}
        params: dict = {}

        if cfg.use_wms:
            params = {
                "service": "WMS",
                "version": "1.1.1",
                "request": "GetMap",
                "layers": cfg.coverage_id,
                "SRS": cfg.crs,
                "BBOX": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                "width": "500",
                "height": "500",
                "format": "image/geotiff",
                "STYLES": "",
                **cfg.extra_params,
            }
        elif cfg.protocol_version == "2.0.1":
            params = {
                "service": "WCS",
                "version": "2.0.1",
                "request": "GetCoverage",
                "CoverageId": cfg.coverage_id,
                "format": cfg.output_format if cfg.output_format != "GeoTIFF" else "image/tiff",
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
            async with httpx.AsyncClient(
                timeout=60, follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (CAS/0.1) AppleWebKit/537.36",
                    "Referer": cfg.wcs_url,
                },
            ) as client:
                resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                content_type = resp.headers.get("content-type", "")

                failed = (
                    resp.status_code >= 400
                    or ("xml" in content_type and "tiff" not in content_type)
                )
                if failed and not cfg.use_wms and cfg.protocol_version == "1.0.0":
                    params["BBOX"] = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
                    resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                    content_type = resp.headers.get("content-type", "")
                    failed = (
                        resp.status_code >= 400
                        or ("xml" in content_type and "tiff" not in content_type)
                    )
                if failed and not cfg.use_wms and cfg.protocol_version == "2.0.1":
                    for wcs_fmt in ("image/geotiff", "GeoTIFF"):
                        params["format"] = wcs_fmt
                        resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                        content_type = resp.headers.get("content-type", "")
                        if resp.status_code == 200 and "xml" not in content_type:
                            failed = False
                            break
                if failed and cfg.use_wms:
                    fallback_combos = [
                        (cfg.crs, "image/tiff"),
                        (cfg.crs, "image/png"),
                    ]
                    if cfg.crs != "EPSG:4326":
                        fallback_combos.append(("EPSG:4326", "image/tiff"))
                        fallback_combos.append(("EPSG:4326", "image/png"))
                    for fb_crs, fb_fmt in fallback_combos:
                        params["format"] = fb_fmt
                        params["SRS"] = fb_crs
                        if fb_crs != "EPSG:4326":
                            from pyproj import Transformer
                            t = Transformer.from_crs("EPSG:4326", fb_crs, always_xy=True)
                            x0, y0 = t.transform(bbox[0], bbox[1])
                            x1, y1 = t.transform(bbox[2], bbox[3])
                            params["BBOX"] = f"{x0},{y0},{x1},{y1}"
                        resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                        content_type = resp.headers.get("content-type", "")
                        if resp.status_code == 200 and "xml" not in content_type and "html" not in content_type:
                            break

                still_failed = (
                    resp.status_code >= 400
                    or ("xml" in content_type and "tiff" not in content_type)
                )
                if still_failed and "/rest/services/" in cfg.wcs_url.lower() and "mapserver" in cfg.wcs_url.lower():
                    export_url = cfg.wcs_url
                    for suffix in ("/WMSServer", "/WCSServer"):
                        if export_url.endswith(suffix):
                            export_url = export_url[: -len(suffix)]
                    if "/rest/services/" not in export_url.lower():
                        import re as _re
                        export_url = _re.sub(r"(?i)/services/", "/rest/services/", export_url, count=1)
                    export_url += "/export"
                    export_params = {
                        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                        "bboxSR": "4326", "imageSR": "4326",
                        "size": "500,500", "format": "png", "f": "image",
                    }
                    resp = await client.get(export_url, params=export_params)
                    content_type = resp.headers.get("content-type", "")
                    still_failed = resp.status_code >= 400

                if resp.status_code != 200:
                    raise DataFormatError(cfg.slug, f"WCS returned {resp.status_code}")
                if "xml" in content_type and "tiff" not in content_type:
                    raise DataFormatError(cfg.slug, f"WCS error: {resp.text[:300]}")
                raster_bytes = resp.content
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(cfg.slug, f"WCS request failed: {e}") from e

        ct_lower = resp.headers.get("content-type", "").lower()
        if "png" in ct_lower or "jpeg" in ct_lower:
            raster_data, transform, nodata, src_crs = _parse_wms_image(
                raster_bytes, bbox, cfg.crs,
            )
        else:
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


ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 9000))

BATHY_VAR = Variable(
    name="elevation", units="m", data_type=DataType.CONTINUOUS,
    valid_range=(-11000, 9000),
    description="Combined land elevation and ocean depth",
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
        coverage_id="GEBCO_Grid",
        variable=Variable(name="bathymetry", units="m", data_type=DataType.CONTINUOUS,
                          valid_range=(-11000, 9000),
                          description="Ocean/land elevation (bathymetry + topography)"),
        resolution_m=500, category="elevation",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open (GEBCO)",
        citation="GEBCO Compilation Group 2024, GEBCO 2024 Grid",
        use_wms=True,
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
        coverage_id="cryogis:Permafrost-Global-PFI",
        variable=Variable(name="permafrost_index", units="index", data_type=DataType.CONTINUOUS,
                          valid_range=(0, 1),
                          description="Permafrost zonation probability index"),
        resolution_m=1000, category="cryosphere",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open", citation="Obu et al. 2019, Global Permafrost Zonation Index",
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
        use_wms=True,
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
        coverage_id="DEM_global_mosaic", variable=BATHY_VAR, resolution_m=1852,
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open (NOAA)", citation="NOAA NCEI, ETOPO 2022 Global Relief Model",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  MORE COUNTRY FILL-INS — second/third layers
# ═══════════════════════════════════════════════════════════════════════

@register("south_korea_soil")
class SouthKoreaSoilConnector(NationalWCSConnector):
    slug = "south_korea_soil"
    display_name = "South Korea Soil Map (RDA)"
    base_url = "https://soil.rda.go.kr/geoserver/wms"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="south_korea_soil",
        display_name="South Korea Detailed Soil Map (RDA)",
        wcs_url="https://soil.rda.go.kr/geoserver/wms",
        coverage_id="rda:soil_detail",
        variable=Variable(name="soil_type", units="class", data_type=DataType.CATEGORICAL,
                          description="South Korean detailed soil classification"),
        resolution_m=25, category="soil",
        bbox=BoundingBox(min_lon=124.6, min_lat=33.1, max_lon=131.9, max_lat=38.6),
        license="Open (RDA)",
        citation="Rural Development Administration, Korea Soil Map",
        use_wms=True,
    )

