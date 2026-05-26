# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Global ecosystems — peatlands, mangroves, potential natural vegetation, albedo via OpenLandMap COGs."""

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

ECO_LAYERS = {
    "peatland": {
        "url": f"{OLM_BASE}/layers1km/lcv_peatland.prob_cifor_p_1km_s0..0cm_2010_v1.0.tif",
        "variable": Variable(
            name="peatland_probability", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Peatland probability from CIFOR global peatland map",
        ),
    },
    "mangrove": {
        "url": f"{OLM_BASE}/layers250m/lcv_mangrove.extent_usgs_p_250m_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="mangrove_cover", units="%",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Mangrove extent fraction (USGS/GMW)",
        ),
    },
    "potential_vegetation": {
        "url": f"{OLM_BASE}/layers1km/lcv_pnv_biome.type_biome6000_c_1km_s0..0cm_2000_v1.0.tif",
        "variable": Variable(
            name="pnv_biome", units="class",
            data_type=DataType.CATEGORICAL,
            description="Potential natural vegetation biome type (BIOME 6000)",
        ),
    },
    "albedo_annual": {
        "url": f"{OLM_BASE}/layers1km/lcv_albedo_modis.mod43b4_m_1km_s0..0cm_2000..2020_v1.0.tif",
        "variable": Variable(
            name="albedo", units="fraction",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1),
            description="Mean annual surface albedo from MODIS MCD43B4",
        ),
    },
    "ecoregion": {
        "url": f"{OLM_BASE}/layers1km/lcv_ecozones_fao.gez2010_c_1km_s0..0cm_2010_v1.0.tif",
        "variable": Variable(
            name="ecozone", units="class",
            data_type=DataType.CATEGORICAL,
            description="FAO Global Ecological Zones (GEZ 2010)",
        ),
    },
    "road_density": {
        "url": f"{OLM_BASE}/layers1km/dtm_road.density_osm_m_1km_s0..0cm_2020_v1.0.tif",
        "variable": Variable(
            name="road_density", units="km/km2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 100),
            description="Road density from OpenStreetMap",
        ),
    },
}


# @register("global_ecosystems")  # Data retired from OpenLandMap S3
class GlobalEcosystemsConnector(STACMixin, BaseConnector):
    slug = "global_ecosystems"
    display_name = "Global Ecosystems & Land Characteristics"
    base_url = "https://s3.eu-central-1.wasabisys.com"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in ECO_LAYERS.items():
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
                citation="OpenLandMap; CIFOR; USGS; FAO; MODIS; OSM",
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

        if layer_key not in ECO_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = ECO_LAYERS[layer_key]
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
