# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""USDA CDL connector — US cropland data layer via CropScape REST API."""

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

CDL_MAJOR_CLASSES = {
    1: "Corn", 2: "Cotton", 3: "Rice", 4: "Sorghum", 5: "Soybeans",
    6: "Sunflower", 10: "Peanuts", 11: "Tobacco", 12: "Sweet corn",
    21: "Barley", 22: "Durum wheat", 23: "Spring wheat", 24: "Winter wheat",
    25: "Other small grains", 26: "Dbl crop winter wheat/soybeans",
    27: "Rye", 28: "Oats", 29: "Millet",
    36: "Alfalfa", 37: "Other hay/non-alfalfa", 41: "Sugarbeets",
    42: "Dry beans", 43: "Potatoes", 44: "Other crops",
    61: "Fallow/idle cropland", 63: "Forest", 64: "Shrubland",
    65: "Barren", 81: "Clouds/no data", 82: "Developed",
    83: "Water", 87: "Wetlands", 88: "Nonag/undefined",
    111: "Open water", 121: "Developed/open space",
    122: "Developed/low intensity", 123: "Developed/med intensity",
    124: "Developed/high intensity", 131: "Barren",
    141: "Deciduous forest", 142: "Evergreen forest", 143: "Mixed forest",
    152: "Shrubland", 176: "Grassland/pasture",
    190: "Woody wetlands", 195: "Herbaceous wetlands",
}

CROP_VAR = Variable(
    name="cropland",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Crop-specific land cover (~130 classes)",
)


@register("usda_cdl")
class USDAcroplandConnector(BaseConnector):
    slug = "usda_cdl"
    display_name = "USDA CDL (Cropland)"
    base_url = "https://nassgeodata.gmu.edu/axis2/services/CDLService"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:cropland",
                provider=self.slug,
                name="USDA Cropland Data Layer 30m",
                description="US 30m crop-specific land cover (annual, 2008-present)",
                variables=[CROP_VAR],
                resolution_m=30,
                crs="EPSG:5070",
                bbox=BoundingBox(min_lon=-130, min_lat=22, max_lon=-64, max_lat=52),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.REST,
                license="Public Domain",
                citation="USDA NASS, Cropland Data Layer",
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

        year = 2023
        if time_range:
            year = time_range.start.year

        # CropScape bbox expects Albers coords, but also accepts lat/lon
        # via the GetCDLFile endpoint with bbox in EPSG:4326
        try:
            resp = await self.client.get(
                "/GetCDLFile",
                params={
                    "year": str(year),
                    "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                    "format": "tiff",
                },
                timeout=120.0,
            )
            if resp.status_code != 200:
                raise DataFormatError(self.slug, f"CropScape returned {resp.status_code}")

            # CropScape returns XML with a URL to the generated TIFF
            import re

            text = resp.text
            url_match = re.search(r"<returnURL>(.*?)</returnURL>", text)
            if not url_match:
                raise DataFormatError(self.slug, f"No returnURL in CropScape response: {text[:300]}")

            tiff_url = url_match.group(1)
            raster_bytes = await self._get_bytes(tiff_url)

        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"CropScape request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {CDL_MAJOR_CLASSES.get(int(k), f"crop_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="cropland", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"CropScape CDL: {year}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))
