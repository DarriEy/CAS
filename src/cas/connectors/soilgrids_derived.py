# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ISRIC SoilGrids 2.0 *derived* layers via global Cloud-Optimized GeoTIFFs.

The ``isric_soilgrids`` connector exposes per-depth soil *properties*
(clay/sand/SOC/pH/…) through the point-query REST API. SoilGrids also ships
two high-value **derived** global products as openly hosted COG/VRT mosaics on
``files.isric.org`` (no authentication required), which the property API does
not return:

* ``ocs``  — Soil Organic Carbon **Stock** for the 0–30 cm layer (t/ha),
  a continuous variable read with a MEAN zonal stat.
* ``wrb``  — Most-probable **WRB Reference Soil Group** (soil taxonomy class),
  a categorical variable read with a DISTRIBUTION zonal stat and a
  pixel-code → soil-group-name mapping.

Both are read with a server-side window via rasterio/GDAL ``vsicurl``. The OCS
mosaic is published in the Interrupted Goode Homolosine projection, so the
windowed read + geometry rasterisation reproject through the shared STAC mixin
helpers (the same CRS handling used by the Planetary Computer COG connectors).
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

DATA_BASE = "https://files.isric.org/soilgrids/latest/data"

# Most-probable WRB Reference Soil Group: pixel value → soil group name.
# Source: files.isric.org/soilgrids/latest/data/wrb/MostProbable.rat.json
WRB_CLASSES = {
    0: "Acrisols", 1: "Albeluvisols", 2: "Alisols", 3: "Andosols",
    4: "Arenosols", 5: "Calcisols", 6: "Cambisols", 7: "Chernozems",
    8: "Cryosols", 9: "Durisols", 10: "Ferralsols", 11: "Fluvisols",
    12: "Gleysols", 13: "Gypsisols", 14: "Histosols", 15: "Kastanozems",
    16: "Leptosols", 17: "Lixisols", 18: "Luvisols", 19: "Nitisols",
    20: "Phaeozems", 21: "Planosols", 22: "Plinthosols", 23: "Podzols",
    24: "Regosols", 25: "Solonchaks", 26: "Solonetz", 27: "Stagnosols",
    28: "Umbrisols", 29: "Vertisols",
}

OCS_VAR = Variable(
    name="ocs",
    units="t/ha",
    data_type=DataType.CONTINUOUS,
    valid_range=(0, 1000),
    description="Soil organic carbon stock, 0-30 cm",
)
WRB_VAR = Variable(
    name="wrb_class",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Most-probable WRB Reference Soil Group (soil taxonomy)",
)

# dataset key → (relative COG/VRT path, Variable)
LAYERS: dict[str, tuple[str, Variable]] = {
    "ocs": ("ocs/ocs_0-30cm_mean.vrt", OCS_VAR),
    "wrb_class": ("wrb/MostProbable.vrt", WRB_VAR),
}


@register("soilgrids_derived")
class SoilGridsDerivedConnector(STACMixin, BaseConnector):
    slug = "soilgrids_derived"
    display_name = "ISRIC SoilGrids 2.0 (derived)"
    base_url = DATA_BASE
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:{key}",
                provider=self.slug,
                name=f"SoilGrids {var.description or var.name}",
                description=(
                    f"Global 250m {var.description or var.name} ({var.units})"
                ),
                variables=[var],
                resolution_m=250,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=84),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="CC-BY-4.0",
                citation="Poggio et al. 2021, SoilGrids 2.0 (ISRIC)",
            )
            for key, (_, var) in LAYERS.items()
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()

        _, _, layer_key = dataset_id.partition(":")
        if layer_key not in LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        rel_path, var = LAYERS[layer_key]
        cog_url = f"{DATA_BASE}/{rel_path}"
        bbox = self._geometry_to_bbox(geometry)

        agg = (
            AggregationMethod.DISTRIBUTION
            if var.data_type == DataType.CATEGORICAL
            else AggregationMethod.MEAN
        )

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_url, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=var.data_type,
        )

        if var.data_type == DataType.CATEGORICAL and isinstance(value, dict):
            value = {
                WRB_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()
            }

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=var.name, value=value,
            units=var.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"COG: SoilGrids/{rel_path}",
        )
