# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Switzerland swissALTI3D connector — 0.5m/2m DEM via swisstopo STAC API."""

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

STAC_URL = "https://data.geo.admin.ch/api/stac/v1"
COLLECTION = "ch.swisstopo.swissalti3d"

ELEV_VAR = Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 5000))


@register("swiss_dem")
class SwissDEMConnector(STACMixin, BaseConnector):
    slug = "swiss_dem"
    display_name = "Switzerland swissALTI3D 0.5m"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="Switzerland swissALTI3D 0.5m",
                description="Swiss national DEM at 0.5m resolution from swisstopo",
                variables=[ELEV_VAR],
                resolution_m=0.5,
                crs="EPSG:2056",
                bbox=BoundingBox(min_lon=5.9, min_lat=45.8, max_lon=10.5, max_lat=47.8),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="Open (swisstopo)", citation="swisstopo, swissALTI3D",
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
                dataset_id=dataset_id, variable="elevation", value=None,
                units="m", aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        assets = item.get("assets", {})
        cog_href = None
        for key in ["swissalti3d_0.5m", "data", list(assets.keys())[0] if assets else ""]:
            if key in assets and assets[key].get("href"):
                cog_href = assets[key]["href"]
                break

        if not cog_href:
            raise DataFormatError(self.slug, "No COG asset found in STAC item")

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
            dataset_id=dataset_id, variable="elevation", value=value,
            units="m", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC: {COLLECTION}/{item.get('id', 'unknown')}",
        )
