# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""GLiM — Global Lithological Map via OpenLandMap STAC+COG."""

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

LITHOLOGY_VAR = Variable(
    name="lithology",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Surface lithology (16 classes: sediments, siliciclastics, carbonates, etc.)",
)

COG_URL = "https://s3.eu-central-1.wasabisys.com/openlandmap/layers1km/sol_lithology_usgs.ecotapestry_c_1km_s0..0cm_2017_v1.0.tif"


# @register("glim")  # Data retired from OpenLandMap S3
class GLiMConnector(STACMixin, BaseConnector):
    slug = "glim"
    display_name = "GLiM Global Lithology 1km"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:lithology",
                provider=self.slug,
                name="Global Lithological Map (GLiM) 1km",
                description="Global surface lithology classification from Hartmann & Moosdorf 2012",
                variables=[LITHOLOGY_VAR],
                resolution_m=1000,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="Hartmann & Moosdorf 2012, The new global lithological map database GLiM",
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

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=COG_URL,
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
            aggregation=AggregationMethod.DISTRIBUTION,
            data_type=DataType.CATEGORICAL,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id,
            variable="lithology",
            value=value,
            units="class",
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance="COG: GLiM Global Lithological Map 1km (OpenLandMap)",
        )
