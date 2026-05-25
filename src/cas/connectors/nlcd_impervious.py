# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""NLCD Imperviousness connector — US 30m impervious surface via MRLC WMS.

Impervious percentage controls infiltration vs surface runoff partitioning.
"""

from __future__ import annotations

import time

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

WMS_URL = "https://www.mrlc.gov/geoserver/mrlc_display/wms"

IMPERV_VAR = Variable(
    name="imperviousness", units="%", data_type=DataType.CONTINUOUS,
    valid_range=(0, 100),
    description="Urban impervious surface percentage",
)

AVAILABLE_YEARS = [2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021]


@register("nlcd_impervious")
class NLCDImperviousConnector(BaseConnector):
    slug = "nlcd_impervious"
    display_name = "NLCD Imperviousness (US)"
    base_url = WMS_URL
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:imperviousness",
                provider=self.slug,
                name="NLCD 30m Imperviousness (US)",
                description="US 30m impervious surface percentage — controls infiltration vs runoff",
                variables=[IMPERV_VAR],
                resolution_m=30,
                crs="EPSG:5070",
                bbox=BoundingBox(min_lon=-130, min_lat=22, max_lon=-64, max_lat=52),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.WCS,
                license="Public Domain",
                citation="USGS MRLC, NLCD Impervious Surface",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = _geometry_to_bbox(geometry)

        year = 2021
        if time_range:
            target = time_range.start.year
            year = min(AVAILABLE_YEARS, key=lambda y: abs(y - target))

        layer = f"NLCD_{year}_Impervious_L48"

        params = {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": layer,
            "bbox": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}",
            "width": "500",
            "height": "500",
            "srs": "EPSG:4326",
            "format": "image/geotiff",
        }

        try:
            resp = await self.client.get("", params=params, timeout=60.0)
            if resp.status_code != 200:
                raise DataFormatError(self.slug, f"WMS returned {resp.status_code}")
            content_type = resp.headers.get("content-type", "")
            if "xml" in content_type or "html" in content_type:
                raise DataFormatError(self.slug, f"WMS error: {resp.text[:300]}")
            raster_bytes = resp.content
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"WMS request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        # NLCD uses 127 or 255 as nodata for imperviousness
        if nodata is None:
            nodata = 255.0

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="imperviousness", value=value,
            units="%", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"WMS: {layer}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))
