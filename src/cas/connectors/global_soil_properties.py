# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Additional global soil properties — soil depth, organic carbon stock, soil water via OpenLandMap COGs."""

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

SOIL_PROP_LAYERS = {
    "soil_depth": {
        "url": f"{OLM_BASE}/layers250m/sol_depth.bedrock_usda.stats_cm_250m_s0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="soil_depth", units="cm",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
            description="Absolute depth to bedrock (predicted, USDA statistics)",
        ),
    },
    "ocs_0_30cm": {
        "url": f"{OLM_BASE}/layers250m/sol_organic.carbon.stock_msa.kgm2_m_250m_b0..30cm_1950..2017_v0.2.tif",
        "variable": Variable(
            name="ocs", units="kg/m2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 500),
            description="Organic carbon stock in 0-30cm layer",
        ),
    },
    "ocs_0_200cm": {
        "url": f"{OLM_BASE}/layers250m/sol_organic.carbon.stock_msa.kgm2_m_250m_b0..200cm_1950..2017_v0.2.tif",
        "variable": Variable(
            name="ocs", units="kg/m2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 2000),
            description="Organic carbon stock in 0-200cm layer",
        ),
    },
    "usda_great_group": {
        "url": f"{OLM_BASE}/layers250m/sol_grtgroup_usda.soiltax_c_250m_s0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="soil_great_group", units="class",
            data_type=DataType.CATEGORICAL,
            description="USDA Soil Taxonomy great group classification",
        ),
    },
    "wrb_soil_class": {
        "url": f"{OLM_BASE}/layers250m/sol_wrb.class_fao.prj_c_250m_s0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="wrb_class", units="class",
            data_type=DataType.CATEGORICAL,
            description="WRB/FAO soil classification (Reference Soil Groups)",
        ),
    },
}


# @register("global_soil_properties")  # Data retired from OpenLandMap S3
class GlobalSoilPropertiesConnector(STACMixin, BaseConnector):
    slug = "global_soil_properties"
    display_name = "Global Soil Properties (OpenLandMap)"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in SOIL_PROP_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Soil {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=250,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-62, max_lon=180, max_lat=87),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="Hengl et al. 2017, SoilGrids250m; OpenLandMap soil layers",
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

        if layer_key not in SOIL_PROP_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = SOIL_PROP_LAYERS[layer_key]
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
