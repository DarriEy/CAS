# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""HREA — High-Res Electricity Access indicators via Planetary Computer STAC+COG."""

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
COLLECTION = "hrea"

ELEC_VAR = Variable(
    name="electricity_access", units="probability",
    data_type=DataType.CONTINUOUS, valid_range=(0, 1),
    description="Settlement-level likelihood of electricity access (nightlights-derived)",
)


@register("hrea")
class HREAConnector(STACMixin, BaseConnector):
    slug = "hrea"
    display_name = "HREA Electricity Access"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:electricity_access",
            provider=self.slug,
            name="High-Res Electricity Access Indicators",
            description="Settlement electricity access probability from nightlights",
            variables=[ELEC_VAR],
            resolution_m=500,
            crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=75),
            temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
            protocol=Protocol.STAC_COG,
            license="CC-BY-4.0",
            citation="Min et al. 2021, High-Resolution Electricity Access Indicators",
        )]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)

        items = await self._stac_search(
            catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox,
        )
        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="electricity_access",
                value=None, units="probability",
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0,
                pixel_count=0, provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "lightscore"
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            asset_key = asset_keys[0] if asset_keys else "lightscore"

        cog_href = item["assets"][asset_key].get("href", "")
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

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN,
            data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="electricity_access",
            value=value, units="probability",
            aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage,
            pixel_count=pixel_count, provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', '?')}",
        )
