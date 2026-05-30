# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Microsoft Building Footprints connector — global building density via Planetary Computer."""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.stac import STACMixin
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

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

BUILDING_VAR = Variable(
    name="building_footprints", units="count", data_type=DataType.CONTINUOUS,
    description="AI-detected building footprint count — proxy for urbanization",
)


@register("ms_buildings")
class MSBuildingsConnector(STACMixin, BaseConnector):
    slug = "ms_buildings"
    display_name = "MS Building Footprints (Global)"
    base_url = STAC_URL
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:buildings", provider=self.slug,
            name="Microsoft Global Building Footprints",
            description="AI-detected building footprints — urbanization proxy for runoff estimation",
            variables=[BUILDING_VAR], resolution_m=1, crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
            temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
            protocol=Protocol.REST, license="ODbL",
            citation="Microsoft, Global ML Building Footprints",
        )]

    async def extract(
        self, dataset_id: str, geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = geometry_to_bbox(geometry)

        items = await self._stac_search(
            catalog_url=STAC_URL, collections=["ms-buildings"], bbox=bbox,
        )
        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="building_footprints",
                value=None, units="count", aggregation=AggregationMethod.SUM,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="ms-buildings (no items)",
            )

        item = self._select_best_item(items)
        building_count = item.get("properties", {}).get("table:row_count")

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return AttributeResult(
            dataset_id=dataset_id, variable="building_footprints",
            value=float(building_count) if building_count else None,
            units="count", aggregation=AggregationMethod.SUM,
            quality=QualityFlag.GOOD if building_count else QualityFlag.MISSING,
            coverage_fraction=1.0 if building_count else 0.0,
            pixel_count=1, provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC: ms-buildings/{item.get('id', '')}",
        )

