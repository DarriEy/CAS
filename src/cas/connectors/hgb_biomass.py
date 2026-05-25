# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""HGB Biomass connector — global harmonized above/belowground biomass via Planetary Computer.

Provider: NASA ORNL DAAC (requires Earthdata login for direct access).
Access via: Planetary Computer STAC+COG (open).
"""

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
COLLECTION = "hgb"

HGB_VARIABLES: dict[str, Variable] = {
    "aboveground": Variable(
        name="aboveground_biomass", units="Mg/ha", data_type=DataType.CONTINUOUS,
        valid_range=(0, 500), description="Aboveground biomass carbon density",
    ),
    "belowground": Variable(
        name="belowground_biomass", units="Mg/ha", data_type=DataType.CONTINUOUS,
        valid_range=(0, 200), description="Belowground biomass carbon density",
    ),
}


@register("hgb_biomass")
class HGBBiomassConnector(STACMixin, BaseConnector):
    slug = "hgb_biomass"
    display_name = "HGB Global Biomass"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, var_meta in HGB_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"HGB {var_meta.description}",
                    description=f"Global ~300m {var_meta.description} ({var_meta.units}, 2010)",
                    variables=[var_meta],
                    resolution_m=300,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=-61, max_lon=180, max_lat=84),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.STAC_COG,
                    license="NASA (open)",
                    citation="Spawn et al. 2020, Harmonized Global Biomass",
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
        bbox = self._geometry_to_bbox(geometry)
        _, _, var_key = dataset_id.partition(":")

        if var_key not in HGB_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        var_meta = HGB_VARIABLES[var_key]

        items = await self._stac_search(
            catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox,
        )

        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable=var_meta.name, value=None,
                units=var_meta.units, aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = var_key
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            if not asset_keys:
                raise DataFormatError(self.slug, "STAC item has no assets")
            asset_key = asset_keys[0]

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

        try:
            import planetary_computer
            cog_href = planetary_computer.sign(cog_href)
        except ImportError:
            pass

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=var_meta.name, value=value,
            units=var_meta.units, aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
