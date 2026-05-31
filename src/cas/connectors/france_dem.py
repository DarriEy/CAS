# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""France IGN RGE ALTI connector — 1m/5m elevation via Geoplateforme REST API.

Point elevation queries — samples a grid of points within the polygon.
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
from cas.extract.zonal import geometry_to_bbox

logger = structlog.get_logger()

ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 5000))


@register("france_dem")
class FranceDEMConnector(BaseConnector):
    slug = "france_dem"
    display_name = "France RGE ALTI 1m (IGN)"
    base_url = "https://data.geopf.fr"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="France RGE ALTI 1m/5m (IGN Geoplateforme)",
                description="French national DEM via point elevation REST API (1m LiDAR / 5m photo)",
                variables=[ELEV_VAR],
                resolution_m=1,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-5.2, min_lat=41.3, max_lon=9.6, max_lat=51.1),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="Open Licence (Etalab)",
                citation="IGN, RGE ALTI (Geoplateforme)",
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

        sample_points = _generate_grid(bbox, 0.001)
        if not sample_points:
            sample_points = [((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)]
        if len(sample_points) > 300:
            step = max(1, len(sample_points) // 300)
            sample_points = sample_points[::step]

        sem = asyncio.Semaphore(5)
        tasks = [self._query_point(lon, lat, sem) for lon, lat in sample_points]
        raw_values = await asyncio.gather(*tasks)

        values = [v for v in raw_values if v is not None]
        total = len(sample_points)
        valid = len(values)
        coverage = valid / total if total > 0 else 0.0

        if valid == 0:
            return AttributeResult(
                dataset_id=dataset_id, variable="elevation", value=None,
                units="m", aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"IGN REST: 0/{total} points",
            )

        mean_val = float(np.mean(values))
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL

        return AttributeResult(
            dataset_id=dataset_id, variable="elevation", value=mean_val,
            units="m", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=valid,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"IGN REST: {valid}/{total} points",
        )

    async def _query_point(self, lon: float, lat: float, sem: asyncio.Semaphore) -> float | None:
        async with sem:
            try:
                resp = await self._get(
                    "/altimetrie/1.0/calcul/alti/rest/elevation.json",
                    params={
                        "lon": str(round(lon, 6)),
                        "lat": str(round(lat, 6)),
                        # Required by the IGN Geoplateforme API; without it every
                        # request is rejected with HTTP 405.
                        "resource": "ign_rge_alti_wld",
                    },
                )
                data = resp.json()
                elevations = data.get("elevations", [])
                if elevations and elevations[0].get("z") is not None:
                    return float(elevations[0]["z"])
                return None
            except Exception:
                return None



def _generate_grid(bbox: tuple[float, float, float, float], spacing: float) -> list[tuple[float, float]]:
    lons = np.arange(bbox[0] + spacing / 2, bbox[2], spacing)
    lats = np.arange(bbox[1] + spacing / 2, bbox[3], spacing)
    if len(lons) == 0:
        lons = np.array([(bbox[0] + bbox[2]) / 2])
    if len(lats) == 0:
        lats = np.array([(bbox[1] + bbox[3]) / 2])
    return [(float(lon), float(lat)) for lon in lons for lat in lats]
