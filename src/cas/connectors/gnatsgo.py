# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""gNATSGO Rasters connector — US 30m gridded soil properties via Planetary Computer.

Pre-computed raster grids of key soil properties — no SQL queries needed.
Variables include available water storage (AWS) and soil organic carbon (SOC)
at multiple depth ranges.
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
COLLECTION = "gnatsgo-rasters"

GNATSGO_VARIABLES: dict[str, Variable] = {
    "mukey": Variable(name="mukey", units="id", data_type=DataType.CATEGORICAL,
                      description="Map unit key"),
    "aws0_5": Variable(name="aws0_5", units="mm", data_type=DataType.CONTINUOUS,
                       valid_range=(0, 100),
                       description="Available water storage 0-5cm"),
    "aws0_20": Variable(name="aws0_20", units="mm", data_type=DataType.CONTINUOUS,
                        valid_range=(0, 200),
                        description="Available water storage 0-20cm"),
    "aws0_30": Variable(name="aws0_30", units="mm", data_type=DataType.CONTINUOUS,
                        valid_range=(0, 300),
                        description="Available water storage 0-30cm"),
    "aws5_20": Variable(name="aws5_20", units="mm", data_type=DataType.CONTINUOUS,
                        valid_range=(0, 200),
                        description="Available water storage 5-20cm"),
    "soc0_5": Variable(name="soc0_5", units="g/m2", data_type=DataType.CONTINUOUS,
                       valid_range=(0, 50000),
                       description="Soil organic carbon 0-5cm"),
    "soc0_20": Variable(name="soc0_20", units="g/m2", data_type=DataType.CONTINUOUS,
                        valid_range=(0, 100000),
                        description="Soil organic carbon 0-20cm"),
    "soc0_30": Variable(name="soc0_30", units="g/m2", data_type=DataType.CONTINUOUS,
                        valid_range=(0, 150000),
                        description="Soil organic carbon 0-30cm"),
    "tk0_5a": Variable(name="tk0_5a", units="cm", data_type=DataType.CONTINUOUS,
                       valid_range=(0, 10),
                       description="Soil thickness 0-5cm (all components)"),
    "tk0_5s": Variable(name="tk0_5s", units="cm", data_type=DataType.CONTINUOUS,
                       valid_range=(0, 10),
                       description="Soil thickness 0-5cm (soil components only)"),
}


@register("gnatsgo")
class GNATSGOConnector(STACMixin, BaseConnector):
    slug = "gnatsgo"
    display_name = "gNATSGO Rasters (US)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, var_meta in GNATSGO_VARIABLES.items():
            if var_key == "mukey":
                continue
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"gNATSGO {var_meta.description}",
                    description=f"US 30m gridded {var_meta.description} ({var_meta.units})",
                    variables=[var_meta],
                    resolution_m=30,
                    crs="EPSG:5070",
                    bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.STAC_COG,
                    license="Public Domain",
                    citation="USDA-NRCS, Gridded National Soil Survey Geographic Database",
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
        if var_key not in GNATSGO_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        var_meta = GNATSGO_VARIABLES[var_key]

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
            raise DataFormatError(self.slug, f"Asset '{var_key}' not in STAC item")

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

        cog_href = self._sign_planetary_computer(cog_href)

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
