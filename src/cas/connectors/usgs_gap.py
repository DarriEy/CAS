# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""USGS Gap Land Cover connector — US ecosystem land cover via Planetary Computer."""

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

LC_VAR = Variable(
    name="land_cover", units="class", data_type=DataType.CATEGORICAL,
    description="GAP/LANDFIRE terrestrial ecosystem classification (~600 types)",
)


@register("usgs_gap")
class USGSGapConnector(STACMixin, BaseConnector):
    slug = "usgs_gap"
    display_name = "USGS Gap Ecosystems (US)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:land_cover", provider=self.slug,
            name="USGS Gap/LANDFIRE Terrestrial Ecosystems 30m",
            description="US 30m detailed ecosystem classification (~600 NatureServe types)",
            variables=[LC_VAR], resolution_m=30, crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-130, min_lat=22, max_lon=-64, max_lat=52),
            temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
            protocol=Protocol.STAC_COG, license="Public Domain",
            citation="USGS GAP/LANDFIRE National Terrestrial Ecosystems",
        )]

    async def extract(
        self, dataset_id: str, geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)
        items = await self._stac_search(catalog_url=STAC_URL, collections=["gap"], bbox=bbox)
        if not items:
            return AttributeResult(dataset_id=dataset_id, variable="land_cover", value=None, units="class",
                                   aggregation=AggregationMethod.DISTRIBUTION, quality=QualityFlag.MISSING,
                                   coverage_fraction=0.0, pixel_count=0, provider=self.slug,
                                   elapsed_ms=int((time.monotonic() - start_time) * 1000), provenance="gap (no items)")
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
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e
        mask = rasterize_geometry(geometry.model_dump(), raster_data.shape, transform, src_crs)
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data, mask, nodata,
            AggregationMethod.DISTRIBUTION, DataType.CATEGORICAL,
        )
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0:
            quality = QualityFlag.MISSING
        return AttributeResult(dataset_id=dataset_id, variable="land_cover", value=value, units="class_fraction",
                               aggregation=AggregationMethod.DISTRIBUTION, quality=quality, coverage_fraction=coverage,
                               pixel_count=pixel_count, provider=self.slug, elapsed_ms=elapsed_ms,
                               provenance=f"STAC COG: gap/{item.get('id', '')}")
