# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""USFS FIA Forest Inventory connector — US forest attributes via Planetary Computer."""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.stac import STACMixin
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
from cas.extract.zonal import compute_zonal_stats, rasterize_geometry

logger = structlog.get_logger()

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

FOREST_VAR = Variable(
    name="forest_group", units="class", data_type=DataType.CATEGORICAL,
    description="USFS Forest Inventory forest type group classification",
)


@register("fia_forest")
class FIAForestConnector(STACMixin, BaseConnector):
    slug = "fia_forest"
    display_name = "USFS FIA Forest Inventory (US)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:forest_group", provider=self.slug,
            name="USFS FIA Forest Type Group 30m",
            description="US 30m forest type group classification (USFS Forest Inventory & Analysis)",
            variables=[FOREST_VAR], resolution_m=30, crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
            temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
            protocol=Protocol.STAC_COG, license="Public Domain",
            citation="USFS Forest Inventory and Analysis",
        )]

    async def extract(
        self, dataset_id: str, geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)
        items = await self._stac_search(
            catalog_url=STAC_URL, collections=["fia"], bbox=bbox,
        )
        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="forest_group",
                value=None, units="class",
                aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0,
                pixel_count=0, provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="fia (no items)",
            )
        item = self._select_best_item(items)
        cog_href = item["assets"].get("data", {}).get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "No data asset")
        try:
            import planetary_computer
            cog_href = planetary_computer.sign(cog_href)
        except ImportError:
            pass
        try:
            raster_data, transform, nodata = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e
        mask = rasterize_geometry(
            geometry.model_dump(), raster_data.shape, transform,
        )
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data, mask, nodata,
            AggregationMethod.DISTRIBUTION, DataType.CATEGORICAL,
        )
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0:
            quality = QualityFlag.MISSING
        return AttributeResult(
            dataset_id=dataset_id, variable="forest_group",
            value=value, units="class_fraction",
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage,
            pixel_count=pixel_count, provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: fia/{item.get('id', '')}",
        )
