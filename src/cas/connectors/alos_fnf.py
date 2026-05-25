# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ALOS Forest/Non-Forest connector — global 25m SAR-based forest mapping.

Provider: JAXA EORC (direct download available).
Access via: Planetary Computer STAC+COG (simpler API).
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
COLLECTION = "alos-fnf-mosaic"

FNF_CLASSES = {
    1: "Forest",
    2: "Non-forest",
    3: "Water",
}

FOREST_VAR = Variable(
    name="forest", units="class", data_type=DataType.CATEGORICAL,
    description="SAR-based forest/non-forest classification (L-band PALSAR)",
)


@register("alos_fnf")
class ALOSForestConnector(STACMixin, BaseConnector):
    slug = "alos_fnf"
    display_name = "ALOS Forest/Non-Forest 25m"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:forest",
                provider=self.slug,
                name="ALOS 25m Forest/Non-Forest",
                description="Global 25m SAR-based forest mapping (JAXA PALSAR, 2015-2020)",
                variables=[FOREST_VAR],
                resolution_m=25,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=85),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="JAXA (free for research)",
                citation="JAXA, ALOS PALSAR Forest/Non-Forest",
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
                dataset_id=dataset_id, variable="forest", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "C"
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
            value = {FNF_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="forest", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
