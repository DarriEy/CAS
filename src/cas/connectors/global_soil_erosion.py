# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global soil erosion and erodibility properties via OpenLandMap COGs."""

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

EROSION_LAYERS = {
    "soil_erodibility": {
        "url": f"{OLM_BASE}/layers250m/sol_kfactor_usda.rosetta_m_250m_s0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="k_factor", units="t*ha*h/(ha*MJ*mm)",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1),
            description="USDA soil erodibility K-factor (Rosetta PTF)",
        ),
    },
    "erosion_potential": {
        "url": f"{OLM_BASE}/layers1km/sol_erosion.potential_usle_m_1km_s0..0cm_2017_v1.0.tif",
        "variable": Variable(
            name="erosion_potential", units="t/ha/yr",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1000),
            description="Potential soil loss by water erosion (USLE model)",
        ),
    },
    "fire_frequency": {
        "url": f"{OLM_BASE}/layers1km/lcv_fire.freq_modis.mcd64a1_p_1km_s0..0cm_2001..2020_v1.0.tif",
        "variable": Variable(
            name="fire_frequency", units="events/20yr",
            data_type=DataType.CONTINUOUS, valid_range=(0, 200),
            description="Fire frequency 2001-2020 from MODIS MCD64A1 burned area",
        ),
    },
    "soil_organic_matter": {
        "url": f"{OLM_BASE}/layers250m/sol_organic.matter_usda.rosetta3_m_250m_b0..0cm_1950..2017_v0.1.tif",
        "variable": Variable(
            name="som", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Topsoil organic matter content (Rosetta3 PTF)",
        ),
    },
}


@register("global_soil_erosion")
class GlobalSoilErosionConnector(STACMixin, BaseConnector):
    slug = "global_soil_erosion"
    display_name = "Global Soil Erosion & Fire"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in EROSION_LAYERS.items():
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
                citation="OpenLandMap; USLE soil erosion; MODIS MCD64A1",
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

        if layer_key not in EROSION_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = EROSION_LAYERS[layer_key]
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
