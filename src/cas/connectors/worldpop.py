# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""WorldPop — high-resolution population density via COG."""

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

POP_VAR = Variable(
    name="population_density", units="persons/100m_cell",
    data_type=DataType.CONTINUOUS, valid_range=(0, 100000),
    description="WorldPop constrained UN-adjusted population count per ~100m cell",
)

COG_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/"
    "BSGM/ppp_2020_1km_Aggregated.tif"
)


# @register("worldpop")  # COG URLs need updating
class WorldPopConnector(STACMixin, BaseConnector):
    slug = "worldpop"
    display_name = "WorldPop Population 1km"
    base_url = "https://data.worldpop.org"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [Dataset(
            id=f"{self.slug}:population",
            provider=self.slug,
            name="WorldPop Population Density 1km",
            description="Global population count per 1km cell (UN-adjusted, 2020)",
            variables=[POP_VAR],
            resolution_m=1000,
            crs="EPSG:4326",
            bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=85),
            temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
            protocol=Protocol.STAC_COG,
            license="CC-BY-4.0",
            citation="WorldPop, University of Southampton (2020)",
        )]

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
                cog_url=COG_URL, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.SUM,
            data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="population_density",
            value=value, units="persons/100m_cell",
            aggregation=AggregationMethod.SUM,
            quality=quality, coverage_fraction=coverage,
            pixel_count=pixel_count, provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance="COG: WorldPop 2020 1km constrained UN-adjusted",
        )
