# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""NLCD connector — US 30m land cover via USGS WCS (GeoServer)."""

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
from cas.extract.zonal import geometry_to_bbox, compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

NLCD_CLASSES = {
    11: "Open water",
    12: "Perennial ice/snow",
    21: "Developed, open space",
    22: "Developed, low intensity",
    23: "Developed, medium intensity",
    24: "Developed, high intensity",
    31: "Barren land",
    41: "Deciduous forest",
    42: "Evergreen forest",
    43: "Mixed forest",
    51: "Dwarf scrub",
    52: "Shrub/scrub",
    71: "Grassland/herbaceous",
    72: "Sedge/herbaceous",
    73: "Lichens",
    74: "Moss",
    81: "Pasture/hay",
    82: "Cultivated crops",
    90: "Woody wetlands",
    95: "Emergent herbaceous wetlands",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Modified Anderson Level II land cover (16 classes)",
)

AVAILABLE_YEARS = [2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021]


@register("nlcd")
class NLCDConnector(BaseConnector):
    slug = "nlcd"
    display_name = "NLCD (US)"
    base_url = "https://www.mrlc.gov/geoserver"
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="NLCD 30m Land Cover (US)",
                description=f"US 30m land cover from MRLC. Years: {AVAILABLE_YEARS}",
                variables=[LAND_COVER_VAR],
                resolution_m=30,
                crs="EPSG:5070",
                bbox=BoundingBox(min_lon=-130, min_lat=22, max_lon=-64, max_lat=52),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.WCS,
                license="Public Domain",
                citation="USGS MRLC, National Land Cover Database",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = geometry_to_bbox(geometry)

        year = 2021
        if time_range:
            target = time_range.start.year
            year = min(AVAILABLE_YEARS, key=lambda y: abs(y - target))

        layer = f"mrlc_download:NLCD_{year}_Land_Cover_L48"
        wcs_url = f"{self.base_url}/mrlc_download/wcs"

        params = {
            "service": "WCS",
            "version": "1.0.0",
            "request": "GetCoverage",
            "coverage": layer,
            "CRS": "EPSG:4326",
            "BBOX": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}",
            "width": "500",
            "height": "500",
            "format": "GeoTIFF",
        }

        try:
            resp = await self.client.get(wcs_url, params=params, timeout=60.0)
            if resp.status_code != 200:
                raise DataFormatError(self.slug, f"WCS returned {resp.status_code}: {resp.text[:200]}")
            content_type = resp.headers.get("content-type", "")
            if "xml" in content_type or "html" in content_type:
                raise DataFormatError(self.slug, f"WCS returned error: {resp.text[:300]}")
            raster_bytes = resp.content
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"WCS request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {NLCD_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"WCS: {layer}",
        )

