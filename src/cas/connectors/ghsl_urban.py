# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""GHSL — Global Human Settlement Layer: built-up, population, settlement via JRC COGs."""

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

GHSL_LAYERS = {
    "built_surface": {
        "collection": "jrc-ghsl-built-s-r2023a",
        "asset": "built_surface",
        "variable": Variable(
            name="built_surface", units="m2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
            description="Built-up surface area per grid cell (GHSL-BUILT-S R2023A)",
        ),
        "resolution_m": 100,
    },
    "population": {
        "collection": "jrc-ghsl-pop-r2023a",
        "asset": "population_count",
        "variable": Variable(
            name="population", units="persons",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1000000),
            description="Population count per grid cell (GHSL-POP R2023A)",
        ),
        "resolution_m": 100,
    },
    "settlement_class": {
        "collection": "jrc-ghsl-smod-r2023a",
        "asset": "smod",
        "variable": Variable(
            name="settlement_type", units="class",
            data_type=DataType.CATEGORICAL,
            description="Settlement model degree of urbanization (GHSL-SMOD R2023A)",
        ),
        "resolution_m": 1000,
    },
}


@register("ghsl")
class GHSLConnector(STACMixin, BaseConnector):
    slug = "ghsl"
    display_name = "GHSL Global Human Settlement 100m"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in GHSL_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"GHSL {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=ds_info["resolution_m"],
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="CC-BY-4.0",
                citation="Pesaresi et al. 2023, JRC GHSL R2023A",
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

        if layer_key not in GHSL_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = GHSL_LAYERS[layer_key]
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
            dataset_id=dataset_id,
            variable=ds_info["variable"].name,
            value=value,
            units=ds_info["variable"].units,
            aggregation=agg,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {ds_info['collection']}/{item.get('id', '?')}",
        )
