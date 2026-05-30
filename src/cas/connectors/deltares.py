# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Deltares connectors — global flood maps and water availability via Planetary Computer."""

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


class _DeltaresBase(STACMixin, BaseConnector):
    _collection: str
    _var: Variable

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:{self._var.name}",
                provider=self.slug,
                name=self.display_name,
                description=self._var.description or self._var.name,
                variables=[self._var],
                resolution_m=1000,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="Deltares (free)",
                citation="Deltares, Global Hydrological Model",
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
            catalog_url=STAC_URL, collections=[self._collection], bbox=bbox,
        )

        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable=self._var.name, value=None,
                units=self._var.units, aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {self._collection} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "data"
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            if not asset_keys:
                raise DataFormatError(self.slug, "STAC item has no assets")
            asset_key = asset_keys[0]

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "No href")

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
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=self._var.name, value=value,
            units=self._var.units, aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {self._collection}/{item.get('id', '')}",
        )


# @register("deltares_floods")  # Needs rasterio bounds/asset fix
class DeltaresFloodsConnector(_DeltaresBase):
    slug = "deltares_floods"
    display_name = "Deltares Global Flood Maps"
    base_url = STAC_URL
    protocol = "stac_cog"
    _collection = "deltares-floods"
    _var = Variable(
        name="flood_depth", units="m", data_type=DataType.CONTINUOUS,
        valid_range=(0, 20),
        description="Global flood inundation depth for various return periods",
    )


# @register("deltares_water")  # Needs rasterio bounds/asset fix
class DeltaresWaterConnector(_DeltaresBase):
    slug = "deltares_water"
    display_name = "Deltares Global Water Availability"
    base_url = STAC_URL
    protocol = "stac_cog"
    _collection = "deltares-water-availability"
    _var = Variable(
        name="water_availability", units="m3/s", data_type=DataType.CONTINUOUS,
        valid_range=(0, 100000),
        description="Modeled water availability (runoff, discharge, storage)",
    )
