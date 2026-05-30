# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""NOAA C-CAP — Coastal Change Analysis Program land cover via Planetary Computer STAC+COG."""

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
COLLECTION = "noaa-c-cap"

LC_VAR = Variable(
    name="coastal_land_cover", units="class",
    data_type=DataType.CATEGORICAL,
    description="NOAA C-CAP coastal land cover (25 classes incl. wetland types)",
)


@register("noaa_ccap")
class NOAACCAPConnector(STACMixin, BaseConnector):
    slug = "noaa_ccap"
    display_name = "NOAA C-CAP Coastal Land Cover 30m"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:coastal_land_cover",
            provider=self.slug,
            name="NOAA C-CAP Coastal Land Cover 30m",
            description="US coastal land cover with detailed wetland types (30m)",
            variables=[LC_VAR],
            resolution_m=30,
            crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
            temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
            protocol=Protocol.STAC_COG,
            license="Public Domain (NOAA)",
            citation="NOAA Office for Coastal Management, C-CAP",
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
                dataset_id=dataset_id, variable="coastal_land_cover",
                value=None, units="class",
                aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0,
                pixel_count=0, provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "data"
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            asset_key = asset_keys[0] if asset_keys else "data"

        cog_href = item["assets"][asset_key].get("href", "")
        cog_href = self._sign_planetary_computer(cog_href)

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
            aggregation=AggregationMethod.DISTRIBUTION,
            data_type=DataType.CATEGORICAL,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="coastal_land_cover",
            value=value, units="class",
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage,
            pixel_count=pixel_count, provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', '?')}",
        )
