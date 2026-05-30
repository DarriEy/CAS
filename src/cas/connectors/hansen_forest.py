# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Hansen Global Forest Change — tree cover, loss, gain via Planetary Computer STAC+COG."""

from __future__ import annotations

import math
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

HANSEN_LAYERS = {
    "treecover2000": {
        "asset": "treecover2000",
        "variable": Variable(
            name="tree_cover_2000", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Tree canopy cover for year 2000",
        ),
    },
    "lossyear": {
        "asset": "lossyear",
        "variable": Variable(
            name="loss_year", units="year_offset",
            data_type=DataType.CATEGORICAL,
            description="Year of forest loss (1=2001, 22=2022, 0=no loss)",
        ),
    },
    "gain": {
        "asset": "gain",
        "variable": Variable(
            name="forest_gain", units="binary",
            data_type=DataType.CATEGORICAL,
            description="Forest gain 2000-2012 (1=gain, 0=no gain)",
        ),
    },
}


@register("hansen_forest")
class HansenForestConnector(STACMixin, BaseConnector):
    slug = "hansen_forest"
    display_name = "Hansen Global Forest Change 30m"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in HANSEN_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Hansen GFC {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=30,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=80),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-4.0",
                citation="Hansen et al. 2013, High-Resolution Global Maps of 21st-Century Forest Cover Change, Science",
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

        if layer_key not in HANSEN_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = HANSEN_LAYERS[layer_key]
        bbox = self._geometry_to_bbox(geometry)

        center_lat = (bbox[1] + bbox[3]) / 2
        center_lon = (bbox[0] + bbox[2]) / 2
        lat_upper = int(math.ceil(center_lat / 10)) * 10
        lon_left = int(math.floor(center_lon / 10)) * 10
        lat_tile = f"{abs(lat_upper):02d}{'N' if lat_upper >= 0 else 'S'}"
        lon_tile = f"{abs(lon_left):03d}{'E' if lon_left >= 0 else 'W'}"

        asset_name = ds_info["asset"]
        cog_url = (
            f"https://storage.googleapis.com/earthenginepartners-hansen/"
            f"GFC-2023-v1.11/Hansen_GFC-2023-v1.11_{asset_name}_{lat_tile}_{lon_tile}.tif"
        )

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_url,
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
            provenance=f"COG: Hansen GFC v1.11 {layer_key}",
        )
