# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global surface water dynamics and drainage density via OpenLandMap COGs."""

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

OLM_BASE = "https://s3.eu-central-1.wasabisys.com/openlandmap"

WATER_LAYERS = {
    "water_occurrence": {
        "url": f"{OLM_BASE}/layers250m/lcv_water.occurance_jrc.surfacewater_p_250m_s0..0cm_1984..2019_v1.0.tif",
        "variable": Variable(
            name="water_occurrence", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Surface water occurrence frequency 1984-2019 (JRC Global Surface Water)",
        ),
    },
    "drainage_density": {
        "url": f"{OLM_BASE}/layers1km/dtm_drainage.density_merit.dem_m_1km_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="drainage_density", units="km/km2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 50),
            description="Drainage density derived from MERIT DEM stream network",
        ),
    },
    "stream_dist": {
        "url": f"{OLM_BASE}/layers250m/dtm_stream.dist_merit.dem_m_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="stream_distance", units="m",
            data_type=DataType.CONTINUOUS, valid_range=(0, 500000),
            description="Distance to nearest stream channel (MERIT DEM derived)",
        ),
    },
    "wetland_potential": {
        "url": f"{OLM_BASE}/layers250m/lcv_wetlands.potential_cifor_c_250m_s0..0cm_2010_v1.0.tif",
        "variable": Variable(
            name="wetland_class", units="class",
            data_type=DataType.CATEGORICAL,
            description="Global wetland potential classification (CIFOR)",
        ),
    },
}


# @register("global_water")  # Data retired from OpenLandMap S3
class GlobalWaterConnector(STACMixin, BaseConnector):
    slug = "global_water"
    display_name = "Global Surface Water & Drainage"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in WATER_LAYERS.items():
            res = 1000 if "1km" in ds_info["url"] else 250
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Global {ds_info['variable'].name.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=res,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-62, max_lon=180, max_lat=87),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="OpenLandMap; JRC Global Surface Water; CIFOR Global Wetlands",
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

        if layer_key not in WATER_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = WATER_LAYERS[layer_key]
        bbox = self._geometry_to_bbox(geometry)

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=ds_info["url"],
                bbox=bbox,
                geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        is_categorical = ds_info["variable"].data_type == DataType.CATEGORICAL
        agg = AggregationMethod.DISTRIBUTION if is_categorical else AggregationMethod.MEAN

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data,
            mask=mask,
            nodata=nodata,
            aggregation=agg,
            data_type=ds_info["variable"].data_type,
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
            provenance=f"COG: OpenLandMap {layer_key}",
        )
