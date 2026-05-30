# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global flood hazard — JRC/Fathom flood maps via Planetary Computer STAC+COG."""

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
from cas.extract.zonal import compute_zonal_stats, rasterize_geometry

logger = structlog.get_logger()

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

FLOOD_LAYERS = {
    "fluvial_undefended_100yr": {
        "collection": "deltares-floods",
        "asset": "data",
        "variable": Variable(
            name="flood_depth_100yr", units="m",
            data_type=DataType.CONTINUOUS, valid_range=(0, 30),
            description="Fluvial flood depth for 100-year return period (undefended)",
        ),
    },
    "pluvial_undefended_100yr": {
        "collection": "deltares-floods",
        "asset": "data",
        "variable": Variable(
            name="pluvial_depth_100yr", units="m",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10),
            description="Pluvial flood depth for 100-year return period",
        ),
    },
    "coastal_undefended_100yr": {
        "collection": "deltares-floods",
        "asset": "data",
        "variable": Variable(
            name="coastal_depth_100yr", units="m",
            data_type=DataType.CONTINUOUS, valid_range=(0, 20),
            description="Coastal flood depth for 100-year return period",
        ),
    },
}


# @register("global_flood_hazard")  # Disabled: all layers use same STAC collection+asset, returning identical data
class GlobalFloodHazardConnector(STACMixin, BaseConnector):
    slug = "global_flood_hazard"
    display_name = "Global Flood Hazard (Deltares/JRC)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in FLOOD_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Flood Hazard {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=90,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=85),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-4.0",
                citation="Deltares / JRC, Global Flood Hazard Maps",
            ))
        return datasets

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        _, _, layer_key = dataset_id.partition(":")

        if layer_key not in FLOOD_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = FLOOD_LAYERS[layer_key]
        bbox = self._geometry_to_bbox(geometry)

        items = await self._stac_search(
            catalog_url=STAC_URL,
            collections=[ds_info["collection"]],
            bbox=bbox,
        )

        if not items:
            return AttributeResult(
                dataset_id=dataset_id,
                variable=ds_info["variable"].name,
                value=None,
                units=ds_info["variable"].units,
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {ds_info['collection']} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = ds_info["asset"]
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            if not asset_keys:
                raise DataFormatError(self.slug, "STAC item has no assets")
            asset_key = asset_keys[0]

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

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
            aggregation=AggregationMethod.MEAN,
            data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id,
            variable=ds_info["variable"].name,
            value=value,
            units=ds_info["variable"].units,
            aggregation=AggregationMethod.MEAN,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {ds_info['collection']}/{item.get('id', '?')}",
        )
