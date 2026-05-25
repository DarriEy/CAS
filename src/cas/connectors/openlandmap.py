# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""OpenLandMap connector — global soil properties via Cloud-Optimized GeoTIFFs on S3.

Reads windowed data from COGs using rasterio/GDAL vsicurl.
Replaces the previous WCS approach (OpenGeoHub GeoServer is offline).
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import structlog

from cas.connectors.base import BaseConnector
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
from cas.extract.zonal import compute_zonal_stats, geometry_to_bbox, rasterize_geometry

logger = structlog.get_logger()

S3_BASE = "https://s3.openlandmap.org/arco"

OLM_VARIABLES: dict[str, tuple[str, Variable]] = {
    "clay": (
        "clay.tot_iso.11277.2020.wpct_m_30m_b0cm..20cm_20000101_20021231_eu_epsg.3035_v20251028.tif",
        Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                 description="Clay content (EU 30m only)"),
    ),
    "sand": (
        "sand.wfraction_usda.3a1a1a_m_250m_b0cm_19500101_20171231_go_epsg.4326_v0.2.tif",
        Variable(name="sand", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "bd": (
        "bulkdens.fineearth_usda.4a1h_m_250m_b0cm_19500101_20171231_go_epsg.4326_v0.2.tif",
        Variable(name="bd", units="10*kg/m3", data_type=DataType.CONTINUOUS, valid_range=(0, 250)),
    ),
    "soc": (
        "organic.carbon_usda.6a1c_m_250m_b0cm_19500101_20171231_go_epsg.4326_v0.2.tif",
        Variable(name="soc", units="5*g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 500)),
    ),
    "ph": (
        "ph.h2o_usda.4c1a2a_m_250m_b0cm_19500101_20171231_go_epsg.4326_v0.2.tif",
        Variable(name="ph", units="pH*10", data_type=DataType.CONTINUOUS, valid_range=(20, 110)),
    ),
    "texture": (
        "texture.class_usda.tt_m_250m_b0cm_19500101_20171231_go_epsg.4326_v0.2.tif",
        Variable(name="texture", units="class", data_type=DataType.CATEGORICAL,
                 description="USDA texture class"),
    ),
    "wc_33kpa": (
        "watercontent.33kPa_usda.4b1c_m_250m_b0cm_19500101_20171231_go_epsg.4326_v0.1.tif",
        Variable(name="wc_33kpa", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                 description="Water content at field capacity (33 kPa)"),
    ),
}

_thread_pool = ThreadPoolExecutor(max_workers=2)


def _read_cog_window(cog_url: str, bbox: tuple[float, float, float, float]):
    """Read a windowed portion of a COG via HTTP range requests (runs in thread)."""
    import rasterio
    from rasterio.windows import from_bounds

    gdal_env = {
        "GDAL_HTTP_TIMEOUT": "30",
        "GDAL_HTTP_MAX_RETRY": "3",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "5000000",
    }

    with rasterio.Env(**gdal_env):
        with rasterio.open(f"/vsicurl/{cog_url}") as src:
            window = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], src.transform)
            data = src.read(1, window=window)
            transform = src.window_transform(window)
            nodata = src.nodata
            crs = src.crs
    return data, transform, nodata, crs


@register("openlandmap")
class OpenLandMapConnector(BaseConnector):
    slug = "openlandmap"
    display_name = "OpenLandMap"
    base_url = S3_BASE
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, (_, var_meta) in OLM_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"OpenLandMap {var_meta.description or var_meta.name}",
                    description=f"Global 250m {var_meta.description or var_meta.name} ({var_meta.units})",
                    variables=[var_meta],
                    resolution_m=250,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=-62, max_lon=180, max_lat=87),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.REST,
                    license="CC-BY-SA 4.0",
                    citation="Hengl et al., OpenLandMap / OpenGeoHub",
                )
            )
        return datasets

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()

        _, _, var_key = dataset_id.partition(":")
        if var_key not in OLM_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        filename, var_meta = OLM_VARIABLES[var_key]
        cog_url = f"{S3_BASE}/{filename}"
        bbox = geometry_to_bbox(geometry)

        import asyncio
        loop = asyncio.get_event_loop()
        try:
            raster_data, transform, nodata, src_crs = await loop.run_in_executor(
                _thread_pool, _read_cog_window, cog_url, bbox
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        agg = AggregationMethod.DISTRIBUTION if var_meta.data_type == DataType.CATEGORICAL else AggregationMethod.MEAN
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=var_meta.data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=var_meta.name, value=value,
            units=var_meta.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"COG: {filename}",
        )

