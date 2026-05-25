# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Depth to bedrock connector — global via ISRIC SoilGrids REST API.

SoilGrids provides absolute depth to bedrock (BDTICM) at 250m resolution.
Controls: vadose zone storage, shallow groundwater, soil moisture capacity.
"""

from __future__ import annotations

import asyncio
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

BDTICM_VAR = Variable(
    name="depth_to_bedrock", units="cm", data_type=DataType.CONTINUOUS,
    valid_range=(0, 10000),
    description="Absolute depth to bedrock (controls vadose zone storage)",
)


@register("depth_to_bedrock")
class DepthToBedrockConnector(BaseConnector):
    slug = "depth_to_bedrock"
    display_name = "Depth to Bedrock (SoilGrids)"
    base_url = "https://rest.isric.org"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:depth",
                provider=self.slug,
                name="Depth to Bedrock 250m",
                description="Global 250m predicted depth to bedrock — controls subsurface storage capacity",
                variables=[BDTICM_VAR],
                resolution_m=250,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=84),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="CC-BY 4.0",
                citation="Shangguan et al. 2017 / ISRIC SoilGrids",
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

        sample_points = _generate_grid(bbox, 0.003)
        if not sample_points:
            sample_points = [((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)]

        # Cap grid size
        if len(sample_points) > 200:
            step = max(1, len(sample_points) // 200)
            sample_points = sample_points[::step]

        sem = asyncio.Semaphore(10)
        tasks = [self._query_point(lon, lat, sem) for lon, lat in sample_points]
        raw_values = await asyncio.gather(*tasks)

        values = [v for v in raw_values if v is not None]
        total = len(sample_points)
        valid = len(values)
        coverage = valid / total if total > 0 else 0.0

        if valid == 0:
            return AttributeResult(
                dataset_id=dataset_id, variable="depth_to_bedrock", value=None,
                units="cm", aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"REST grid sample: 0/{total} points",
            )

        mean_val = float(np.mean(values))
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL

        return AttributeResult(
            dataset_id=dataset_id, variable="depth_to_bedrock", value=mean_val,
            units="cm", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=valid,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"REST grid sample: {valid}/{total} points",
        )

    async def _query_point(self, lon: float, lat: float, sem: asyncio.Semaphore) -> float | None:
        async with sem:
            try:
                resp = await self._get(
                    "/soilgrids/v2.0/properties/query",
                    params={
                        "lon": str(round(lon, 5)),
                        "lat": str(round(lat, 5)),
                        "property": "bdticm",
                        "value": "mean",
                    },
                )
                data = resp.json()
                val = data["properties"]["layers"][0]["depths"][0]["values"]["mean"]
                return float(val) if val is not None else None
            except Exception:
                return None


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


def _generate_grid(bbox: tuple[float, float, float, float], spacing: float) -> list[tuple[float, float]]:
    lons = np.arange(bbox[0] + spacing / 2, bbox[2], spacing)
    lats = np.arange(bbox[1] + spacing / 2, bbox[3], spacing)
    if len(lons) == 0:
        lons = np.array([(bbox[0] + bbox[2]) / 2])
    if len(lats) == 0:
        lats = np.array([(bbox[1] + bbox[3]) / 2])
    return [(float(lon), float(lat)) for lon in lons for lat in lats]
