# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Dynamic World connector — global 10m near-real-time land cover via Google Earth Engine.

Requires Google Cloud project with Earth Engine enabled:
  https://earthengine.google.com/
Set credentials via service account JSON:
  export CAS_GEE_SERVICE_ACCOUNT_KEY=/path/to/service-account-key.json
Or authenticate interactively:
  earthengine authenticate
"""

from __future__ import annotations

import os

import structlog

from cas.connectors.base import BaseConnector
from cas.core.exceptions import RegistrationRequiredError
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

REGISTRATION_URL = "https://earthengine.google.com/"

DW_CLASSES = {
    0: "Water",
    1: "Trees",
    2: "Grass",
    3: "Flooded vegetation",
    4: "Crops",
    5: "Shrub and scrub",
    6: "Built",
    7: "Bare",
    8: "Snow and ice",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="9-class IPCC-aligned land cover from Sentinel-2 (near-real-time)",
)


@register("dynamic_world")
class DynamicWorldConnector(BaseConnector):
    slug = "dynamic_world"
    display_name = "Dynamic World 10m (Google)"
    base_url = "https://earthengine.googleapis.com"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="Dynamic World 10m Land Cover",
                description="Global 10m near-real-time land cover from Google (Sentinel-2 based, 9 classes)",
                variables=[LAND_COVER_VAR],
                resolution_m=10,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.DAILY),
                protocol=Protocol.REST,
                license="CC-BY-4.0",
                citation="Brown et al. 2022, Dynamic World",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        sa_key = self.config.get("service_account_key") or os.environ.get(
            "CAS_GEE_SERVICE_ACCOUNT_KEY", ""
        )
        if not sa_key:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Google Earth Engine access required.\n"
                "1. Create a Google Cloud project and enable Earth Engine API\n"
                "2. Create a service account with Earth Engine scope\n"
                "3. Download the JSON key file\n"
                "4. Set the path:\n"
                "   export CAS_GEE_SERVICE_ACCOUNT_KEY=/path/to/key.json\n"
                "Or authenticate interactively: earthengine authenticate",
            )

        try:
            import ee
        except ImportError as exc:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "The 'earthengine-api' package is required:\n"
                "  pip install earthengine-api\n"
                "Then authenticate: earthengine authenticate",
            ) from exc

        try:
            credentials = ee.ServiceAccountCredentials("", key_file=sa_key)
            ee.Initialize(credentials)
        except Exception as e:
            raise RegistrationRequiredError(
                self.slug, REGISTRATION_URL,
                f"Earth Engine initialization failed: {e}",
            ) from e

        import time as time_mod

        start_time = time_mod.monotonic()
        bbox = _geometry_to_bbox(geometry)

        start_date = "2023-01-01"
        end_date = "2023-12-31"
        if time_range:
            start_date = time_range.start.strftime("%Y-%m-%d")
            end_date = time_range.end.strftime("%Y-%m-%d")

        region = ee.Geometry.Rectangle([bbox[0], bbox[1], bbox[2], bbox[3]])

        dw = (
            ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
            .filterDate(start_date, end_date)
            .filterBounds(region)
            .select("label")
            .mode()
        )

        stats = dw.reduceRegion(
            reducer=ee.Reducer.frequencyHistogram(),
            geometry=region,
            scale=10,
            maxPixels=1e7,
        )

        histogram = stats.getInfo().get("label", {})

        if not histogram:
            return AttributeResult(
                dataset_id=dataset_id, variable="land_cover", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time_mod.monotonic() - start_time) * 1000),
                provenance=f"GEE Dynamic World: {start_date}/{end_date}",
            )

        total = sum(histogram.values())
        value = {
            DW_CLASSES.get(int(float(k)), f"class_{k}"): round(v / total, 4)
            for k, v in histogram.items()
        }

        elapsed_ms = int((time_mod.monotonic() - start_time) * 1000)

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=QualityFlag.GOOD, coverage_fraction=1.0,
            pixel_count=int(total), provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"GEE Dynamic World: {start_date}/{end_date}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))
