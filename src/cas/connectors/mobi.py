# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MoBI — Map of Biodiversity Importance (US) via Planetary Computer STAC+COG."""

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
COLLECTION = "mobi"

MOBI_LAYERS = {
    "species_richness": {
        "asset": "RSR",
        "variable": Variable(
            name="species_richness", units="index",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1),
            description="Range-size rarity-weighted species richness (all taxa)",
        ),
    },
    "protection_deficit": {
        "asset": "PADUS_GAP12",
        "variable": Variable(
            name="protection_status", units="class",
            data_type=DataType.CATEGORICAL,
            description="PAD-US GAP 1/2 protection status",
        ),
    },
}


@register("mobi")
class MoBIConnector(STACMixin, BaseConnector):
    slug = "mobi"
    display_name = "MoBI Biodiversity Importance (US)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in MOBI_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"MoBI {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=990,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-4.0",
                citation="NatureServe, Map of Biodiversity Importance",
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

        if layer_key not in MOBI_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = MOBI_LAYERS[layer_key]
        bbox = self._geometry_to_bbox(geometry)

        items = await self._stac_search(
            catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox,
        )
        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable=ds_info["variable"].name,
                value=None, units=ds_info["variable"].units,
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0,
                pixel_count=0, provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = ds_info["asset"]
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            asset_key = asset_keys[0] if asset_keys else ds_info["asset"]

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

        is_categorical = ds_info["variable"].data_type == DataType.CATEGORICAL
        agg = AggregationMethod.DISTRIBUTION if is_categorical else AggregationMethod.MEAN

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=ds_info["variable"].data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=ds_info["variable"].name,
            value=value, units=ds_info["variable"].units,
            aggregation=agg, quality=quality,
            coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', '?')}",
        )
