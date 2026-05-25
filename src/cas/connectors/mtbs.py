# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MTBS Burn Severity connector — US 30m fire history via Planetary Computer STAC+COG.

Provider: USGS/USFS MTBS (mtbs.gov). Direct download available but not API-friendly.
Access via: Planetary Computer STAC.
"""

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
COLLECTION = "mtbs"

SEVERITY_CLASSES = {
    1: "Unburned/low severity",
    2: "Low severity",
    3: "Moderate severity",
    4: "High severity",
    5: "Increased greenness",
    6: "Non-processing area",
}

BURN_SEVERITY_VAR = Variable(
    name="burn_severity", units="class", data_type=DataType.CATEGORICAL,
    description="Thematic burn severity from Landsat dNBR (6 classes)",
)


@register("mtbs")
class MTBSConnector(STACMixin, BaseConnector):
    slug = "mtbs"
    display_name = "MTBS Burn Severity (US)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:burn_severity",
                provider=self.slug,
                name="MTBS 30m Burn Severity (US)",
                description="US 30m fire burn severity 1984-present (USGS/USFS)",
                variables=[BURN_SEVERITY_VAR],
                resolution_m=30,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-167, min_lat=17, max_lon=-64, max_lat=72),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="Public Domain",
                citation="USGS/USFS, Monitoring Trends in Burn Severity",
            )
        ]

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
                dataset_id=dataset_id, variable="burn_severity", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "burn-severity"
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            if not asset_keys:
                raise DataFormatError(self.slug, "STAC item has no assets")
            asset_key = asset_keys[0]

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

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
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {SEVERITY_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="burn_severity", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
