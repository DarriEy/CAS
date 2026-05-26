# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global soil hydraulic properties — Ksat, porosity, soil water index via OpenLandMap COGs."""

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

OLM_BASE = "https://s3.eu-central-1.wasabisys.com/openlandmap/layers250m"

HYDRAULIC_LAYERS = {
    "ksat_0cm": {
        "url": f"{OLM_BASE}/sol_ksat_usda.rosetta3_m_250m_b0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="ksat", units="cm/day",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
            description="Saturated hydraulic conductivity at 0cm (USDA Rosetta3 PTF)",
        ),
    },
    "ksat_30cm": {
        "url": f"{OLM_BASE}/sol_ksat_usda.rosetta3_m_250m_b30..30cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="ksat", units="cm/day",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
            description="Saturated hydraulic conductivity at 30cm",
        ),
    },
    "ksat_100cm": {
        "url": f"{OLM_BASE}/sol_ksat_usda.rosetta3_m_250m_b100..100cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="ksat", units="cm/day",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
            description="Saturated hydraulic conductivity at 100cm",
        ),
    },
    "porosity_0cm": {
        "url": f"{OLM_BASE}/sol_watercontent.33kPa_usda.rosetta3_m_250m_b0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="porosity", units="cm3/cm3",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Volumetric water content at saturation (porosity) at 0cm",
        ),
    },
    "awc_0cm": {
        "url": f"{OLM_BASE}/sol_watercontent.33kPa_usda.rosetta3_m_250m_b0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="awc", units="cm3/cm3",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Available water capacity (FC minus WP) at 0cm",
        ),
    },
    "usda_texture_class": {
        "url": f"{OLM_BASE}/sol_texture.class_usda.tt_m_250m_b0..0cm_1950..2017_v0.2.tif",
        "variable": Variable(
            name="texture_class", units="class",
            data_type=DataType.CATEGORICAL,
            description="USDA soil texture class (12 classes: clay, silt loam, sandy loam, etc.)",
        ),
    },
}


# @register("soil_hydraulic")  # Data retired from OpenLandMap S3
class SoilHydraulicConnector(STACMixin, BaseConnector):
    slug = "soil_hydraulic"
    display_name = "Global Soil Hydraulic Properties 250m"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in HYDRAULIC_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"Soil Hydraulic {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=250,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-62, max_lon=180, max_lat=87),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="Hengl et al. 2021, OpenLandMap soil hydraulic properties (Rosetta3 PTF)",
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

        if layer_key not in HYDRAULIC_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = HYDRAULIC_LAYERS[layer_key]
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
