# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Copernicus Global Land Cover 100m — discrete classes + fractional covers via STAC+COG."""

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

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "cop-dem-glo-30"  # placeholder — actual collection ID below

GLC_DATASETS = {
    "discrete_classification": {
        "asset": "discrete_classification",
        "variable": Variable(
            name="land_cover", units="class",
            data_type=DataType.CATEGORICAL,
            description="Discrete land cover classification (23 classes)",
        ),
        "data_type": DataType.CATEGORICAL,
    },
    "tree_cover_fraction": {
        "asset": "tree-coverfraction",
        "variable": Variable(
            name="tree_cover_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent tree cover per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
    "shrub_cover_fraction": {
        "asset": "shrub-coverfraction",
        "variable": Variable(
            name="shrub_cover_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent shrub cover per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
    "grass_cover_fraction": {
        "asset": "grass-coverfraction",
        "variable": Variable(
            name="grass_cover_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent herbaceous cover per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
    "cropland_cover_fraction": {
        "asset": "crops-coverfraction",
        "variable": Variable(
            name="cropland_cover_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent cropland cover per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
    "bare_cover_fraction": {
        "asset": "bare-coverfraction",
        "variable": Variable(
            name="bare_cover_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent bare/sparse vegetation per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
    "water_permanent_fraction": {
        "asset": "water-permanent-coverfraction",
        "variable": Variable(
            name="water_permanent_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent permanent water per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
    "snow_cover_fraction": {
        "asset": "snow-coverfraction",
        "variable": Variable(
            name="snow_cover_fraction", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Percent permanent snow/ice per pixel",
        ),
        "data_type": DataType.CONTINUOUS,
    },
}

STAC_COLLECTION = "io-lulc-annual-v02"  # Copernicus GLC is not on PC; use IO LULC or ESA


# @register("cop_glc_100")  # Cloudferro S3 URL changed
class CopernicusGLC100Connector(STACMixin, BaseConnector):
    slug = "cop_glc_100"
    display_name = "Copernicus Global Land Cover 100m"
    base_url = "https://s3.waw3-1.cloudferro.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in GLC_DATASETS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Copernicus GLC {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=100,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=80),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="CC-BY-4.0",
                citation="Buchhorn et al. 2020, Copernicus Global Land Cover Layers v3.0.1",
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

        if layer_key not in GLC_DATASETS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = GLC_DATASETS[layer_key]
        bbox = self._geometry_to_bbox(geometry)

        base = "https://s3.waw3-1.cloudferro.com/swift/v1/AUTH_3f7e5dd853f54cebb046a29a69f1bba6"
        year = 2019
        lon_center = (bbox[0] + bbox[2]) / 2
        lat_center = (bbox[1] + bbox[3]) / 2
        lon_tile = f"{'W' if lon_center < 0 else 'E'}{abs(int(lon_center // 20) * 20):03d}"
        lat_tile = f"{'S' if lat_center < 0 else 'N'}{abs(int(lat_center // 20) * 20):02d}"
        asset_name = ds_info["asset"]

        cog_url = (
            f"{base}/Vegetation/CoverFraction/v3.0.1/{year}/"
            f"{lon_tile}{lat_tile}/{lon_tile}{lat_tile}_PROBAV_LC100_global_v3.0.1_{year}"
            f"__{asset_name}-layer_EPSG-4326.tif"
        )

        if layer_key == "discrete_classification":
            cog_url = (
                f"{base}/Vegetation/CoverFraction/v3.0.1/{year}/"
                f"{lon_tile}{lat_tile}/{lon_tile}{lat_tile}_PROBAV_LC100_global_v3.0.1_{year}"
                f"__discrete-classification_EPSG-4326.tif"
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

        data_type = ds_info["data_type"]
        agg = AggregationMethod.DISTRIBUTION if data_type == DataType.CATEGORICAL else AggregationMethod.MEAN

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data,
            mask=mask,
            nodata=nodata,
            aggregation=agg,
            data_type=data_type,
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
            provenance=f"COG: Copernicus GLC v3.0.1 {year} {layer_key}",
        )
