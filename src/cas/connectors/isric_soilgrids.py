# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ISRIC SoilGrids 2.0 connector — global 250m soil properties via REST API."""

from __future__ import annotations

import asyncio
import time

import numpy as np
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

logger = structlog.get_logger()

SOILGRIDS_VARIABLES: dict[str, Variable] = {
    "clay": Variable(name="clay", units="g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 1000)),
    "sand": Variable(name="sand", units="g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 1000)),
    "silt": Variable(name="silt", units="g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 1000)),
    "soc": Variable(name="soc", units="dg/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 6000)),
    "phh2o": Variable(name="phh2o", units="pH*10", data_type=DataType.CONTINUOUS, valid_range=(20, 120)),
    "bdod": Variable(name="bdod", units="cg/cm3", data_type=DataType.CONTINUOUS, valid_range=(0, 2500)),
    "cec": Variable(name="cec", units="mmol(c)/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 5000)),
    "nitrogen": Variable(name="nitrogen", units="cg/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 500)),
    "cfvo": Variable(name="cfvo", units="cm3/dm3", data_type=DataType.CONTINUOUS, valid_range=(0, 1000),
                     description="Coarse fragments volumetric"),
    "ocd": Variable(name="ocd", units="hg/m3", data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
                    description="Organic carbon density"),
    "wv0010": Variable(name="wv0010", units="0.1 v%", data_type=DataType.CONTINUOUS, valid_range=(0, 1000),
                       description="Volumetric water content at 10 kPa (near saturation)"),
    "wv0033": Variable(name="wv0033", units="0.1 v%", data_type=DataType.CONTINUOUS, valid_range=(0, 1000),
                       description="Volumetric water content at 33 kPa (field capacity)"),
    "wv1500": Variable(name="wv1500", units="0.1 v%", data_type=DataType.CONTINUOUS, valid_range=(0, 1000),
                       description="Volumetric water content at 1500 kPa (wilting point)"),
}

DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"]

GRID_SPACING_DEG = 0.003  # ~250m at mid-latitudes, matching native resolution


@register("isric_soilgrids")
class ISRICSoilGridsConnector(BaseConnector):
    slug = "isric_soilgrids"
    display_name = "ISRIC SoilGrids 2.0"
    base_url = "https://rest.isric.org"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_name, var_meta in SOILGRIDS_VARIABLES.items():
            for depth in DEPTHS:
                ds_id = f"{self.slug}:{var_name}_{depth}"
                datasets.append(
                    Dataset(
                        id=ds_id,
                        provider=self.slug,
                        name=f"SoilGrids {var_name} at {depth}",
                        description=f"Global 250m {var_name} ({var_meta.units}) at depth {depth}",
                        variables=[var_meta],
                        resolution_m=250,
                        crs="EPSG:4326",
                        bbox=BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=84),
                        temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                        protocol=Protocol.REST,
                        license="CC-BY-4.0",
                        citation="Poggio et al. 2021, SoilGrids 2.0",
                    )
                )
        return datasets

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()

        _, _, layer_id = dataset_id.partition(":")
        parts = layer_id.rsplit("_", 1)
        var_name = parts[0]
        depth = parts[1] if len(parts) > 1 else "0-5cm"

        if var_name not in SOILGRIDS_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_name}")

        var_meta = SOILGRIDS_VARIABLES[var_name]
        bbox = _geometry_to_bbox(geometry)

        sample_points = _generate_grid_points(bbox, GRID_SPACING_DEG)
        if not sample_points:
            sample_points = [((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)]

        sem = asyncio.Semaphore(10)
        tasks = [
            self._query_point(lon, lat, var_name, depth, sem) for lon, lat in sample_points
        ]
        raw_values = await asyncio.gather(*tasks)

        values = [v for v in raw_values if v is not None]
        total_points = len(sample_points)
        valid_count = len(values)
        coverage = valid_count / total_points if total_points > 0 else 0.0

        if valid_count == 0:
            return AttributeResult(
                dataset_id=dataset_id,
                variable=var_name,
                value=None,
                units=var_meta.units,
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"REST grid sample: {total_points} points, 0 valid",
            )

        arr = np.array(values, dtype=np.float64)
        mean_value = float(np.mean(arr))

        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL

        return AttributeResult(
            dataset_id=dataset_id,
            variable=var_name,
            value=mean_value,
            units=var_meta.units,
            aggregation=AggregationMethod.MEAN,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=valid_count,
            provider=self.slug,
            elapsed_ms=int((time.monotonic() - start_time) * 1000),
            provenance=f"REST grid sample: {valid_count}/{total_points} points",
        )

    async def _query_point(
        self,
        lon: float,
        lat: float,
        variable: str,
        depth: str,
        sem: asyncio.Semaphore,
    ) -> float | None:
        async with sem:
            try:
                resp = await self._get(
                    "/soilgrids/v2.0/properties/query",
                    params={
                        "lon": str(round(lon, 5)),
                        "lat": str(round(lat, 5)),
                        "property": variable,
                        "depth": depth,
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


def _generate_grid_points(
    bbox: tuple[float, float, float, float],
    spacing: float,
) -> list[tuple[float, float]]:
    """Generate a regular grid of sample points within the bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lons = np.arange(min_lon + spacing / 2, max_lon, spacing)
    lats = np.arange(min_lat + spacing / 2, max_lat, spacing)
    if len(lons) == 0:
        lons = np.array([(min_lon + max_lon) / 2])
    if len(lats) == 0:
        lats = np.array([(min_lat + max_lat) / 2])
    # Cap at reasonable grid size
    max_points = 500
    if len(lons) * len(lats) > max_points:
        step = max(1, int(np.sqrt(len(lons) * len(lats) / max_points)))
        lons = lons[::step]
        lats = lats[::step]
    return [(float(lon), float(lat)) for lon in lons for lat in lats]
