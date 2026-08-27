# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""National WCS/WMS DEM and soil connectors — factory pattern for countries with OGC services.

Each country is a thin registration on top of a shared WCS extraction flow.
This avoids duplicating the same WCS GetCoverage / WMS GetMap logic across 20+ files.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import dataclass, field

import httpx
import structlog

from cas.connectors.base import BaseConnector
from cas.core.exceptions import DataFormatError, RegistrationRequiredError
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

# Registration details for credential-gated services, keyed by the env var
# that holds the token. Used to raise a clear RegistrationRequiredError
# *before* a tokenless request hits the server (and gets a bare 401/403).
_REGISTRATION: dict[str, tuple[str, str]] = {
    "CAS_MML_API_KEY": (
        "https://www.maanmittauslaitos.fi/rajapinnat/api-avaimen-ohje",
        "Register for a free API key, then:\n  export CAS_MML_API_KEY=your_key",
    ),
    "CAS_DATAFORSYNINGEN_TOKEN": (
        "https://dataforsyningen.dk/",
        "Create a user and API token, then:\n  export CAS_DATAFORSYNINGEN_TOKEN=your_token",
    ),
    "CAS_BKG_UUID": (
        "https://gdz.bkg.bund.de/",
        "Request a free UUID access token, then:\n  export CAS_BKG_UUID=your_uuid",
    ),
}


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
    wms_version: str = "1.1.1"
    default_time: str = ""
    # Optional (lon, lat) the health check should sample instead of the generic
    # land anchor — for layers whose data only exists in narrow locations
    # (e.g. mangroves on coastlines) where the default anchor lands on nodata.
    health_anchor: tuple[float, float] | None = None
    # Optional mapping of RGB(A) tuples to categorical integer values.
    # Used for connectors that only provide pre-rendered symbology images.
    color_map: dict[tuple[int, int, int], int] | None = None
    tls_verify: bool = True
    tls_ca_bundle: str = ""
    allow_insecure_tls: bool = False


def _tls_verify(cfg: NationalDatasetConfig) -> bool | ssl.SSLContext:
    """Build the narrowly scoped httpx TLS setting for one provider."""
    if cfg.tls_ca_bundle:
        return ssl.create_default_context(cafile=cfg.tls_ca_bundle)
    if not cfg.tls_verify and not cfg.allow_insecure_tls:
        raise ValueError(f"{cfg.slug}: disabling TLS verification requires allow_insecure_tls=True")
    return cfg.tls_verify


_RETRYABLE_STATUS = (500, 502, 503, 504)
# Connection-level drops that fail fast and are usually transient (server closed
# the socket mid-handshake / before responding). Cheap to retry. Read timeouts are
# deliberately NOT retried — they're slow and would bloat the health sweep.
_RETRYABLE_EXC = (httpx.RemoteProtocolError, httpx.ConnectError)


async def _get_with_retry(client, url, params, headers, retries=2, backoff=0.5):
    """GET with a small retry on transient 5xx or connection drops."""
    resp = None
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, params=params, headers=headers)
        except _RETRYABLE_EXC:
            if attempt == retries:
                raise
            await asyncio.sleep(backoff * (attempt + 1))
            continue
        if resp.status_code in _RETRYABLE_STATUS and attempt < retries:
            await asyncio.sleep(backoff * (attempt + 1))
            continue
        return resp
    return resp


def _wms_params(
    version: str, layers: str, crs: str, fmt: str, bbox: tuple[float, float, float, float], extra: dict
) -> dict:
    """Build WMS GetMap params for the given version.

    WMS 1.1.1 uses ``SRS=`` with min_x,min_y,max_x,max_y order. WMS 1.3.0 uses
    ``CRS=`` and, for geographic CRSs like EPSG:4326, lat,lon axis order. ``bbox``
    is (min_lon, min_lat, max_lon, max_lat) for 4326, or projected min/max for a
    projected ``crs``.
    """
    if version == "1.3.0":
        crs_key = "CRS"
        if crs.upper() == "EPSG:4326":
            bbox_str = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"
        else:
            bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    else:
        crs_key = "SRS"
        bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    return {
        "service": "WMS",
        "version": version,
        "request": "GetMap",
        "layers": layers,
        crs_key: crs,
        "BBOX": bbox_str,
        "width": "500",
        "height": "500",
        "format": fmt,
        "STYLES": "",
        **extra,
    }


def _parse_wms_image(
    data: bytes,
    bbox: tuple[float, float, float, float],
    crs: str = "EPSG:4326",
    color_map: dict[tuple[int, int, int], int] | None = None,
):
    """Convert a WMS GetMap PNG/JPEG response to raster array + transform."""
    from io import BytesIO

    import numpy as np
    from PIL import Image
    from rasterio.transform import from_bounds

    img = Image.open(BytesIO(data))
    arr = np.array(img)
    nodata = 0

    if color_map:
        # Ensure we are working with RGB bytes even if the input was indexed or RGBA.
        img_rgb = img.convert("RGB")
        arr_rgb = np.array(img_rgb)
        h, w, _ = arr_rgb.shape
        # Default to 65535 (nodata) to avoid collision with low-integer classes.
        out = np.full((h, w), 65535, dtype=np.uint16)
        nodata = 65535

        # Map colors from the config to their categorical values.
        for color, val in color_map.items():
            target = np.array(color, dtype=np.uint8)
            mask = np.all(arr_rgb == target, axis=-1)
            out[mask] = val
        arr = out
    elif arr.ndim == 3:
        # Default: just take the first band (e.g. for greyscale GeoTIFFs served as PNG).
        arr = arr[:, :, 0]

    # The transform is built from the request bbox, which is always EPSG:4326
    # degrees in this codebase — so the raster's CRS is 4326 regardless of the
    # layer's native `crs`. Returning `crs` here would make rasterize_geometry
    # reproject the geometry into metres against a degree transform (empty mask).
    transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], arr.shape[1], arr.shape[0])
    return arr, transform, nodata, "EPSG:4326"


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
                    f"{cfg.display_name} ({cfg.resolution_m}m) — {cfg.variable.description or cfg.variable.name}"
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

        token = os.environ.get(cfg.auth_token_env, "") if cfg.auth_token_env else ""
        if cfg.auth_token_env and not token:
            url, instructions = _REGISTRATION.get(
                cfg.auth_token_env,
                ("", f"Set the {cfg.auth_token_env} environment variable."),
            )
            raise RegistrationRequiredError(cfg.slug, url, instructions)

        headers: dict[str, str] = {}
        params: dict = {}

        if cfg.use_wms:
            # WMS GetMap expects the BBOX in the requested SRS. `bbox` is always
            # EPSG:4326 degrees here, so for a projected layer CRS we must
            # reproject — otherwise the server reads degree coords as metres and
            # returns a blank (but HTTP-200) image, which never trips the
            # failed-status fallback below. Regressed norway_dem (EPSG:25833).
            wms_bbox = bbox
            if cfg.crs.upper() != "EPSG:4326":
                from pyproj import Transformer

                t = Transformer.from_crs("EPSG:4326", cfg.crs, always_xy=True)
                x0, y0 = t.transform(bbox[0], bbox[1])
                x1, y1 = t.transform(bbox[2], bbox[3])
                wms_bbox = (x0, y0, x1, y1)
            # color_map connectors want the server's pre-rendered symbology PNG
            # (which _parse_wms_image inverts back to class codes). Requesting a
            # GeoTIFF for those would return raw RGB-derived bytes or, for some
            # MapServer layers, an all-nodata raster — so ask for PNG up front.
            primary_fmt = "image/png" if cfg.color_map else "image/geotiff"
            params = _wms_params(
                cfg.wms_version,
                cfg.coverage_id,
                cfg.crs,
                primary_fmt,
                wms_bbox,
                cfg.extra_params,
            )
        elif cfg.protocol_version == "2.0.1":
            if cfg.crs != "EPSG:4326":
                from pyproj import Transformer

                t = Transformer.from_crs("EPSG:4326", cfg.crs, always_xy=True)
                x0, y0 = t.transform(bbox[0], bbox[1])
                x1, y1 = t.transform(bbox[2], bbox[3])
                subsets = [f"x({x0},{x1})", f"y({y0},{y1})"]
            else:
                subsets = [f"Long({bbox[0]},{bbox[2]})", f"Lat({bbox[1]},{bbox[3]})"]
            if time_range:
                subsets.append(f'time("{time_range.start.strftime("%Y-%m-%dT%H:%M:%SZ")}")')
            elif cfg.default_time:
                subsets.append(f'time("{cfg.default_time}")')
            params = {
                "service": "WCS",
                "version": "2.0.1",
                "request": "GetCoverage",
                "CoverageId": cfg.coverage_id,
                "format": cfg.output_format if cfg.output_format != "GeoTIFF" else "image/tiff",
                "subset": subsets,
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

        if token:
            params["token"] = token

        try:
            async with httpx.AsyncClient(
                timeout=60,
                follow_redirects=True,
                verify=_tls_verify(cfg),
                headers={
                    "User-Agent": "Mozilla/5.0 (CAS/0.1) AppleWebKit/537.36",
                    "Referer": cfg.wcs_url,
                },
            ) as client:
                resp = await _get_with_retry(client, cfg.wcs_url, params, headers)
                content_type = resp.headers.get("content-type", "")

                failed = resp.status_code >= 400 or ("xml" in content_type and "tiff" not in content_type)
                if failed and not cfg.use_wms and cfg.protocol_version == "1.0.0":
                    params["BBOX"] = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
                    resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                    content_type = resp.headers.get("content-type", "")
                    failed = resp.status_code >= 400 or ("xml" in content_type and "tiff" not in content_type)
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
                        fb_bbox = bbox
                        if fb_crs != "EPSG:4326":
                            from pyproj import Transformer

                            t = Transformer.from_crs("EPSG:4326", fb_crs, always_xy=True)
                            x0, y0 = t.transform(bbox[0], bbox[1])
                            x1, y1 = t.transform(bbox[2], bbox[3])
                            fb_bbox = (x0, y0, x1, y1)
                        params = _wms_params(
                            cfg.wms_version,
                            cfg.coverage_id,
                            fb_crs,
                            fb_fmt,
                            fb_bbox,
                            cfg.extra_params,
                        )
                        if token:
                            params["token"] = token
                        resp = await client.get(cfg.wcs_url, params=params, headers=headers)
                        content_type = resp.headers.get("content-type", "")
                        if resp.status_code == 200 and "xml" not in content_type and "html" not in content_type:
                            break

                still_failed = resp.status_code >= 400 or ("xml" in content_type and "tiff" not in content_type)
                arcgis_url = cfg.wcs_url.lower()
                is_arcgis = "mapserver" in arcgis_url or "imageserver" in arcgis_url
                has_arcgis_server = is_arcgis and "/services/" in arcgis_url
                if still_failed and has_arcgis_server:
                    export_url = cfg.wcs_url
                    for suffix in ("/WMSServer", "/WCSServer"):
                        if export_url.endswith(suffix):
                            export_url = export_url[: -len(suffix)]
                    if "/rest/services/" not in export_url.lower():
                        import re as _re

                        export_url = _re.sub(r"(?i)/services/", "/rest/services/", export_url, count=1)
                    if "imageserver" in export_url.lower():
                        export_url += "/exportImage"
                    else:
                        export_url += "/export"
                    export_params = {
                        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                        "bboxSR": "4326",
                        "imageSR": "4326",
                        # Keep the request coarse: scale-cached ArcGIS services (e.g.
                        # BGR soil) render nothing below ~44 m/px, and 100 px over the
                        # small test window stays above that while giving ample samples.
                        "size": "100,100",
                        "format": "png",
                        "f": "image",
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
        # Sniff the body, not just content-type: several ArcGIS/MapServer WMS
        # endpoints return a PNG/JPEG body under an "image/tiff" content-type.
        # Feeding that to parse_geotiff yields an identity transform + crs=None,
        # so the polygon maps off-raster and coverage comes out 0.
        is_image = (
            raster_bytes[:4] == b"\x89PNG"
            or raster_bytes[:3] == b"\xff\xd8\xff"
            or "png" in ct_lower
            or "jpeg" in ct_lower
        )
        if is_image:
            raster_data, transform, nodata, src_crs = _parse_wms_image(
                raster_bytes,
                bbox,
                cfg.crs,
                cfg.color_map,
            )
        else:
            raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        # WMS/WCS GeoTIFFs often omit the embedded CRS; fall back to the
        # configured CRS so the geometry is reprojected to the raster's grid.
        # Only when the raster is genuinely georeferenced — a non-georeferenced
        # tiff returns the identity transform, and reprojecting the geometry into
        # a metric CRS there would push it off-grid (regressing e.g. norway_dem).
        if src_crs is None and transform is not None and not getattr(transform, "is_identity", False):
            src_crs = cfg.crs
        if cfg.nodata_value is not None:
            nodata = cfg.nodata_value

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        is_categorical = cfg.variable.data_type == DataType.CATEGORICAL
        agg = AggregationMethod.DISTRIBUTION if is_categorical else AggregationMethod.MEAN
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data,
            mask=mask,
            nodata=nodata,
            aggregation=agg,
            data_type=cfg.variable.data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id,
            variable=cfg.variable.name,
            value=value,
            units=cfg.variable.units,
            aggregation=agg,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=cfg.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"WCS: {cfg.coverage_id}",
        )


ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 9000))

BATHY_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
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
        slug="gebco",
        display_name="GEBCO Global Bathymetry 500m",
        wcs_url="https://wms.gebco.net/mapserv",
        coverage_id="GEBCO_Grid",
        variable=Variable(
            name="bathymetry",
            units="m",
            data_type=DataType.CONTINUOUS,
            valid_range=(-11000, 9000),
            description="Ocean/land elevation (bathymetry + topography)",
        ),
        resolution_m=500,
        category="elevation",
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
        # UZH's GeoServer dropped WCS 1.0.0 ("Could not understand
        # version:1.0.0", 2026-08); WCS 2.0.1 names coverages with a
        # double-underscore workspace separator.
        coverage_id="cryogis__Permafrost-Global-PFI",
        protocol_version="2.0.1",
        variable=Variable(
            name="permafrost_index",
            units="index",
            data_type=DataType.CONTINUOUS,
            valid_range=(0, 1),
            description="Permafrost zonation probability index",
        ),
        resolution_m=1000,
        category="cryosphere",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open",
        citation="Obu et al. 2019, Global Permafrost Zonation Index",
        # Permafrost index is ~0/nodata over temperate anchors. Pin continuous
        # permafrost in northern Yakutia (Siberia) so the check samples real data.
        health_anchor=(129.0, 67.5),
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
        variable=Variable(
            name="ramsar_site",
            units="class",
            data_type=DataType.CATEGORICAL,
            description="Ramsar Convention designated wetland locations",
        ),
        resolution_m=1000,
        category="hydrology",
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
        slug="etopo_2022",
        display_name="ETOPO 2022 60s Global Relief (NOAA/NCEI)",
        wcs_url="https://gis.ngdc.noaa.gov/arcgis/services/DEM_mosaics/DEM_global_mosaic/ImageServer/WCSServer",
        coverage_id="DEM_global_mosaic",
        variable=BATHY_VAR,
        resolution_m=1852,
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        license="Open (NOAA)",
        citation="NOAA NCEI, ETOPO 2022 Global Relief Model",
        use_wms=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  MORE COUNTRY FILL-INS — second/third layers
# ═══════════════════════════════════════════════════════════════════════


# Disabled: RDA GeoServer removed; soil.rda.go.kr returns an HTML portal stub
# for all OGC requests (no raster body to parse). Verified 2026-05-30.
# @register("south_korea_soil")
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
        variable=Variable(
            name="soil_type",
            units="class",
            data_type=DataType.CATEGORICAL,
            description="South Korean detailed soil classification",
        ),
        resolution_m=25,
        category="soil",
        bbox=BoundingBox(min_lon=124.6, min_lat=33.1, max_lon=131.9, max_lat=38.6),
        license="Open (RDA)",
        citation="Rural Development Administration, Korea Soil Map",
        use_wms=True,
    )
