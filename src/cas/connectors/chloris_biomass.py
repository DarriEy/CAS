# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Chloris Biomass connector — global biomass stock + change 2003-2019 via Planetary Computer."""

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
COLLECTION = "chloris-biomass"

CHLORIS_VARS: dict[str, Variable] = {
    "biomass": Variable(name="biomass", units="Mg/ha", data_type=DataType.CONTINUOUS,
                        valid_range=(0, 500), description="Aboveground biomass carbon stock"),
    "biomass_change": Variable(name="biomass_change", units="Mg/ha/yr", data_type=DataType.CONTINUOUS,
                               valid_range=(-50, 50), description="Annual biomass carbon change rate"),
}


@register("chloris_biomass")
class ChlorisBiomassConnector(STACMixin, BaseConnector):
    slug = "chloris_biomass"
    display_name = "Chloris Biomass (Global)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, var_meta in CHLORIS_VARS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{var_key}", provider=self.slug,
                name=f"Chloris {var_meta.description}",
                description=f"Global ~4km {var_meta.description} (2003-2019)",
                variables=[var_meta], resolution_m=4000, crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG, license="CC-BY 4.0",
                citation="Chloris Global Biomass 2003-2019",
            ))
        return datasets

    async def extract(
        self, dataset_id: str, geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        _, _, var_key = dataset_id.partition(":")
        if var_key not in CHLORIS_VARS:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")
        var_meta = CHLORIS_VARS[var_key]
        bbox = self._geometry_to_bbox(geometry)
        items = await self._stac_search(catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox)
        if not items:
            return AttributeResult(dataset_id=dataset_id, variable=var_meta.name, value=None, units=var_meta.units,
                                   aggregation=AggregationMethod.MEAN, quality=QualityFlag.MISSING,
                                   coverage_fraction=0.0, pixel_count=0, provider=self.slug,
                                   elapsed_ms=int((time.monotonic() - start_time) * 1000),
                                   provenance=f"{COLLECTION} (no items)")
        item = self._select_best_item(items)
        asset_key = var_key
        if asset_key not in item.get("assets", {}):
            all_assets = item.get("assets", {})
            asset_keys = [k for k in all_assets if k not in ("tilejson", "rendered_preview")]
            asset_key = asset_keys[0] if asset_keys else next(iter(all_assets))
        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "No href")
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
        mask = rasterize_geometry(geometry.model_dump(), raster_data.shape, transform, src_crs)
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data, mask, nodata,
            AggregationMethod.MEAN, DataType.CONTINUOUS,
        )
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0:
            quality = QualityFlag.MISSING
        return AttributeResult(dataset_id=dataset_id, variable=var_meta.name, value=value, units=var_meta.units,
                               aggregation=AggregationMethod.MEAN, quality=quality, coverage_fraction=coverage,
                               pixel_count=pixel_count, provider=self.slug, elapsed_ms=elapsed_ms,
                               provenance=f"STAC COG: {COLLECTION}/{item.get('id', '')}")
