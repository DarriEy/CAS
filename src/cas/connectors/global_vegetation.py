# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global vegetation indices — NDVI, LAI, FAPAR composites via OpenLandMap COGs."""

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

VEG_LAYERS = {
    "ndvi_annual_mean": {
        "url": f"{OLM_BASE}/layers250m/veg_ndvi_modis.mod13a2_m_250m_s0..0cm_2000..2022_v1.0.tif",
        "variable": Variable(
            name="ndvi_mean", units="index",
            data_type=DataType.CONTINUOUS, valid_range=(-1, 1),
            description="Long-term mean annual NDVI from MODIS MOD13A2",
        ),
    },
    "fapar_annual_mean": {
        "url": f"{OLM_BASE}/layers250m/veg_fapar_proba.v.v2_m_250m_s0..0cm_2014..2020_v1.0.tif",
        "variable": Variable(
            name="fapar_mean", units="fraction",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1),
            description="Long-term mean FAPAR from PROBA-V",
        ),
    },
    "lai_annual_mean": {
        "url": f"{OLM_BASE}/layers1km/veg_lai_modis.mod15a2h_m_1km_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="lai_mean", units="m2/m2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10),
            description="Long-term mean LAI from MODIS MOD15A2H",
        ),
    },
    "tree_cover_pct": {
        "url": f"{OLM_BASE}/layers250m/veg_tree.cover_modis.mod44b_p_250m_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="tree_cover", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="MODIS VCF tree cover percentage (long-term mean)",
        ),
    },
    "non_tree_veg_pct": {
        "url": f"{OLM_BASE}/layers250m/veg_non.tree.veg_modis.mod44b_p_250m_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="non_tree_vegetation", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="MODIS VCF non-tree vegetation percentage",
        ),
    },
    "bare_ground_pct": {
        "url": f"{OLM_BASE}/layers250m/veg_non.veg_modis.mod44b_p_250m_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="bare_ground", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="MODIS VCF non-vegetated (bare ground) percentage",
        ),
    },
}


@register("global_vegetation")
class GlobalVegetationConnector(STACMixin, BaseConnector):
    slug = "global_vegetation"
    display_name = "Global Vegetation Indices (MODIS/PROBA-V)"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in VEG_LAYERS.items():
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
                temporal=TemporalExtent(temporal_type=TemporalType.CLIMATOLOGY),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA-4.0",
                citation="Hengl et al. 2021, OpenLandMap vegetation layers",
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

        if layer_key not in VEG_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = VEG_LAYERS[layer_key]
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
