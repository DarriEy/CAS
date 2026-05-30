# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global cropland and irrigation maps via OpenLandMap COGs."""

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

CROP_LAYERS = {
    "cropland_extent": {
        "url": f"{OLM_BASE}/layers250m/lcv_cropland.extent_glad.gfsad_p_250m_s0..0cm_2015_v1.0.tif",
        "variable": Variable(
            name="cropland_extent", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Cropland extent from GLAD GFSAD (2015)",
        ),
    },
    "irrigated_area": {
        "url": f"{OLM_BASE}/layers1km/lcv_irrigation_fao.gmia_p_1km_s0..0cm_2005_v1.0.tif",
        "variable": Variable(
            name="irrigated_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Irrigated area fraction (FAO GMIA)",
        ),
    },
    "pasture_extent": {
        "url": f"{OLM_BASE}/layers250m/lcv_pasture.extent_hyde3.2_p_250m_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="pasture_extent", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Pasture/grazing land extent (HYDE 3.2)",
        ),
    },
    "crop_type_dominant": {
        "url": f"{OLM_BASE}/layers1km/lcv_crop.type_earthstat_c_1km_s0..0cm_2000_v1.0.tif",
        "variable": Variable(
            name="crop_type", units="class",
            data_type=DataType.CATEGORICAL,
            description="Dominant crop type (EarthStat, ~175 crop types)",
        ),
    },
}


# @register("global_crops")  # Data retired from OpenLandMap S3
class GlobalCropsConnector(STACMixin, BaseConnector):
    slug = "global_crops"
    display_name = "Global Cropland & Irrigation"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in CROP_LAYERS.items():
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
                citation="OpenLandMap; GLAD GFSAD; FAO GMIA; HYDE 3.2; EarthStat",
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

        if layer_key not in CROP_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = CROP_LAYERS[layer_key]
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
