# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""NRCan Land Cover of Canada connector — 30m via Planetary Computer STAC + COG."""

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
COLLECTION = "nrcan-landcover"

NRCAN_CLASSES = {
    1: "Temperate/sub-polar needleleaf forest",
    2: "Sub-polar taiga needleleaf forest",
    5: "Temperate/sub-polar broadleaf deciduous forest",
    6: "Mixed forest",
    8: "Temperate/sub-polar shrubland",
    10: "Temperate/sub-polar grassland",
    11: "Sub-polar/polar shrubland-lichen-moss",
    12: "Sub-polar/polar grassland-lichen-moss",
    13: "Sub-polar/polar barren-lichen-moss",
    14: "Wetland",
    15: "Cropland",
    16: "Barren lands",
    17: "Urban",
    18: "Water",
    19: "Snow and ice",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Canadian land cover classification (15 classes)",
)


@register("nrcan_lc")
class NRCanLandCoverConnector(STACMixin, BaseConnector):
    slug = "nrcan_lc"
    display_name = "NRCan Land Cover (Canada)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="Canada Land Cover 30m",
                description="Canada 30m land cover from Natural Resources Canada (2015-2020)",
                variables=[LAND_COVER_VAR],
                resolution_m=30,
                crs="EPSG:3978",
                bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="Open Government Licence - Canada",
                citation="Natural Resources Canada, Land Cover of Canada",
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
                dataset_id=dataset_id, variable="land_cover", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "landcover"
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
            raster_data, transform, nodata = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {NRCAN_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
