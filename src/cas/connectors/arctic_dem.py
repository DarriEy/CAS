# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ArcticDEM connector — Arctic 10m/32m elevation via PGC STAC + COG."""

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

PGC_STAC_URL = "https://stac.pgc.umn.edu/api/v1"
COLLECTION = "arcticdem-mosaics"

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 7000),
)


@register("arctic_dem")
class ArcticDEMConnector(STACMixin, BaseConnector):
    slug = "arctic_dem"
    display_name = "ArcticDEM 10m"
    base_url = PGC_STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="ArcticDEM Mosaic 10m Elevation",
                description="Arctic 10m DEM mosaic from the Polar Geospatial Center",
                variables=[ELEVATION_VAR],
                resolution_m=10,
                crs="EPSG:3413",
                bbox=BoundingBox(min_lon=-180, min_lat=50, max_lon=180, max_lat=84),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="Open (PGC)",
                citation="Porter et al., ArcticDEM, Polar Geospatial Center",
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
            catalog_url=PGC_STAC_URL,
            collections=[COLLECTION],
            bbox=bbox,
        )

        if not items:
            return AttributeResult(
                dataset_id=dataset_id,
                variable="elevation",
                value=None,
                units="m",
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items found)",
            )

        item = self._select_best_item(items)
        asset_key = "dem"
        if asset_key not in item.get("assets", {}):
            for candidate in ["data", "elevation"]:
                if candidate in item.get("assets", {}):
                    asset_key = candidate
                    break
            else:
                asset_keys = list(item.get("assets", {}).keys())
                if not asset_keys:
                    raise DataFormatError(self.slug, "STAC item has no assets")
                asset_key = asset_keys[0]

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href,
                bbox=bbox,
                geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data,
            mask=mask,
            nodata=nodata,
            aggregation=AggregationMethod.MEAN,
            data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id,
            variable="elevation",
            value=value,
            units="m",
            aggregation=AggregationMethod.MEAN,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
