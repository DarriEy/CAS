# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Japan GSI DEM connector — 5-10m elevation via XYZ tiles from GSI."""

from __future__ import annotations

import contextlib
import time

import numpy as np
import structlog

from cas.connectors.base import BaseConnector
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

logger = structlog.get_logger()

ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 4000))


@register("japan_gsi")
class JapanGSIConnector(BaseConnector):
    slug = "japan_gsi"
    display_name = "Japan GSI DEM 5-10m"
    base_url = "https://cyberjapandata.gsi.go.jp"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="Japan GSI DEM 5-10m",
                description="Japan national DEM (5m DEM5A / 10m DEM10B) via XYZ text tiles",
                variables=[ELEV_VAR],
                resolution_m=5,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=122, min_lat=24, max_lon=154, max_lat=46),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="Open (GSI)", citation="Geospatial Information Authority of Japan",
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

        center_lon = (bbox[0] + bbox[2]) / 2
        center_lat = (bbox[1] + bbox[3]) / 2

        # Use zoom level 14 for ~5m resolution
        zoom = 14
        tile_x, tile_y = _latlon_to_tile(center_lat, center_lon, zoom)

        # Try DEM5A first, fall back to DEM10B
        value = None
        provenance = ""
        for dem_type in ["dem5a_png", "dem_png"]:
            url = f"{self.base_url}/xyz/{dem_type}/{zoom}/{tile_x}/{tile_y}.txt"
            try:
                resp = await self._get(url)
                elevations = _parse_dem_txt(resp.text)
                if elevations:
                    value = float(np.mean(elevations))
                    provenance = f"GSI {dem_type} z{zoom}/{tile_x}/{tile_y}"
                    break
            except Exception:
                continue

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if value is None:
            return AttributeResult(
                dataset_id=dataset_id, variable="elevation", value=None,
                units="m", aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug, elapsed_ms=elapsed_ms, provenance="GSI tiles (no data)",
            )

        return AttributeResult(
            dataset_id=dataset_id, variable="elevation", value=value,
            units="m", aggregation=AggregationMethod.MEAN,
            quality=QualityFlag.GOOD, coverage_fraction=1.0,
            pixel_count=len(elevations), provider=self.slug,
            elapsed_ms=elapsed_ms, provenance=provenance,
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    import math
    n = 2**zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _parse_dem_txt(text: str) -> list[float]:
    values = []
    for line in text.strip().split("\n"):
        for val in line.split(","):
            val = val.strip()
            if val and val != "e":
                with contextlib.suppress(ValueError):
                    values.append(float(val))
    return values
