# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global terrain derivatives — slope, TWI, TPI, VBF from MERIT DEM via OpenLandMap COGs."""

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

OLM_BASE = "https://s3.eu-central-1.wasabisys.com/openlandmap"

TERRAIN_LAYERS = {
    "slope": {
        "url": f"{OLM_BASE}/layers250m/dtm_slope_merit.dem_m_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="slope", units="degrees",
            data_type=DataType.CONTINUOUS, valid_range=(0, 90),
            description="Slope gradient derived from MERIT DEM",
        ),
    },
    "twi": {
        "url": f"{OLM_BASE}/layers250m/dtm_twi_merit.dem_m_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="twi", units="index",
            data_type=DataType.CONTINUOUS, valid_range=(0, 30),
            description="SAGA topographic wetness index from MERIT DEM",
        ),
    },
    "tpi": {
        "url": f"{OLM_BASE}/layers250m/dtm_tpi_merit.dem_m_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="tpi", units="index",
            data_type=DataType.CONTINUOUS, valid_range=(-1000, 1000),
            description="Topographic position index from MERIT DEM",
        ),
    },
    "vbf": {
        "url": f"{OLM_BASE}/layers250m/dtm_vbf_merit.dem_m_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="vbf", units="index",
            data_type=DataType.CONTINUOUS, valid_range=(0, 15),
            description="Multi-resolution valley bottom flatness from MERIT DEM",
        ),
    },
    "convergence": {
        "url": f"{OLM_BASE}/layers250m/dtm_convergence_merit.dem_m_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="convergence", units="index",
            data_type=DataType.CONTINUOUS, valid_range=(-100, 100),
            description="Convergence index (negative=convergent/valley, positive=divergent/ridge)",
        ),
    },
    "landform": {
        "url": f"{OLM_BASE}/layers250m/dtm_landform_usgs.ecotapestry_c_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="landform", units="class",
            data_type=DataType.CATEGORICAL,
            description="Geomorphological landform classification (USGS ecotapestry)",
        ),
    },
}


# @register("terrain_derivatives")  # Disabled: OpenLandMap S3 data retired
class TerrainDerivativesConnector(STACMixin, BaseConnector):
    slug = "terrain_derivatives"
    display_name = "Global Terrain Derivatives 250m (MERIT DEM)"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in TERRAIN_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Terrain {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=250,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-62, max_lon=180, max_lat=87),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="Amatulli et al. 2020, Geomorpho90m; OpenLandMap MERIT DEM derivatives",
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

        if layer_key not in TERRAIN_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = TERRAIN_LAYERS[layer_key]
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
            provenance=f"COG: OpenLandMap terrain {layer_key}",
        )
