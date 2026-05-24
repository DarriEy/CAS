# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Biodiversity Intactness Index connector — global via Planetary Computer STAC+COG."""

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

BII_VAR = Variable(
    name="biodiversity_intactness", units="index", data_type=DataType.CONTINUOUS,
    valid_range=(0, 1),
    description="Biodiversity Intactness Index — measures ecosystem health",
)


@register("biodiversity")
class BiodiversityConnector(STACMixin, BaseConnector):
    slug = "biodiversity"
    display_name = "Biodiversity Intactness (Global)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:bii", provider=self.slug,
            name="Biodiversity Intactness Index 100m",
            description="Global 100m biodiversity intactness (0-1, Impact Observatory)",
            variables=[BII_VAR], resolution_m=100, crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
            temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
            protocol=Protocol.STAC_COG, license="CC-BY 4.0",
            citation="Impact Observatory, Biodiversity Intactness",
        )]

    async def extract(
        self, dataset_id: str, geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)
        items = await self._stac_search(
            catalog_url=STAC_URL, collections=["io-biodiversity"], bbox=bbox,
        )
        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="biodiversity_intactness",
                value=None, units="index", aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="io-biodiversity (no items)",
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
        mask = rasterize_geometry(geometry.model_dump(), raster_data.shape, transform)
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data, mask, nodata,
            AggregationMethod.MEAN, DataType.CONTINUOUS,
        )
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0:
            quality = QualityFlag.MISSING
        return AttributeResult(
            dataset_id=dataset_id, variable="biodiversity_intactness",
            value=value, units="index", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: io-biodiversity/{item.get('id', '')}",
        )
