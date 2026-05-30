# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global glacier cover and ice sheet extent via OpenLandMap COGs."""

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

GLACIER_LAYERS: dict[str, dict] = {
    "glacier_cover": {
        "url": f"{OLM_BASE}/layers1km/lcv_glacier.cover_rgi6.0_p_1km_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="glacier_cover", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Glacier cover fraction from Randolph Glacier Inventory 6.0",
        ),
    },
    "snow_prob": {
        "url": f"{OLM_BASE}/layers1km/lcv_snow.prob_modis.mod10a2_p_1km_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="snow_probability", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Mean annual snow cover probability from MODIS MOD10A2",
        ),
    },
}


# @register("glacier_cover")  # Data retired from OpenLandMap S3
class GlacierCoverConnector(STACMixin, BaseConnector):
    slug = "glacier_cover"
    display_name = "Global Glacier & Snow Cover"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in GLACIER_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Global {ds_info['variable'].name.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=1000,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="RGI Consortium 2017; MODIS MOD10A2; via OpenLandMap",
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

        if layer_key not in GLACIER_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = GLACIER_LAYERS[layer_key]
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
            variable=ds_info["variable"].name,
            value=value,
            units=ds_info["variable"].units,
            aggregation=AggregationMethod.MEAN,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"COG: OpenLandMap {layer_key}",
        )
