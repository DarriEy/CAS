# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""POLARIS connector — US 30m probabilistic soil properties via HTTP tile download.

Uniquely provides hydraulic properties: Ksat, van Genuchten parameters, Brooks-Corey.
Tiles are 1x1 degree GeoTIFFs organized by variable/statistic/depth.
"""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
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
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

BASE_URL = "http://hydrology.cee.duke.edu/POLARIS/PROPERTIES/v1.0"

POLARIS_VARIABLES: dict[str, Variable] = {
    "clay": Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    "sand": Variable(name="sand", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    "silt": Variable(name="silt", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    "bd": Variable(name="bd", units="g/cm3", data_type=DataType.CONTINUOUS, valid_range=(0.5, 2.5),
                   description="Bulk density"),
    "om": Variable(name="om", units="log10(%)", data_type=DataType.CONTINUOUS, valid_range=(-3, 3),
                   description="Organic matter"),
    "ph": Variable(name="ph", units="pH", data_type=DataType.CONTINUOUS, valid_range=(3, 10)),
    "ksat": Variable(name="ksat", units="log10(cm/hr)", data_type=DataType.CONTINUOUS, valid_range=(-3, 4),
                     description="Saturated hydraulic conductivity"),
    "theta_s": Variable(name="theta_s", units="m3/m3", data_type=DataType.CONTINUOUS, valid_range=(0, 1),
                        description="Saturated water content"),
    "theta_r": Variable(name="theta_r", units="m3/m3", data_type=DataType.CONTINUOUS, valid_range=(0, 0.5),
                        description="Residual water content"),
    "n": Variable(name="n", units="dimensionless", data_type=DataType.CONTINUOUS, valid_range=(1, 5),
                  description="van Genuchten pore size distribution"),
    "alpha": Variable(name="alpha", units="log10(kPa-1)", data_type=DataType.CONTINUOUS, valid_range=(-4, 2),
                      description="van Genuchten scale parameter"),
    "hb": Variable(name="hb", units="log10(kPa)", data_type=DataType.CONTINUOUS, valid_range=(-2, 4),
                   description="Brooks-Corey bubbling pressure"),
    "lambda": Variable(name="lambda", units="dimensionless", data_type=DataType.CONTINUOUS, valid_range=(0, 3),
                       description="Brooks-Corey pore size distribution index"),
}

DEPTHS = ["0_5", "5_15", "15_30", "30_60", "60_100", "100_200"]
DEPTH_LABELS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"]


@register("polaris")
class POLARISConnector(BaseConnector):
    slug = "polaris"
    display_name = "POLARIS 30m (US)"
    base_url = BASE_URL
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_name, var_meta in POLARIS_VARIABLES.items():
            for _depth_code, depth_label in zip(DEPTHS, DEPTH_LABELS):
                ds_id = f"{self.slug}:{var_name}_{depth_label}"
                datasets.append(
                    Dataset(
                        id=ds_id,
                        provider=self.slug,
                        name=f"POLARIS {var_name} at {depth_label}",
                        description=f"US 30m {var_meta.description or var_name} ({var_meta.units}) at {depth_label}",
                        variables=[var_meta],
                        resolution_m=30,
                        crs="EPSG:4326",
                        bbox=BoundingBox(min_lon=-125, min_lat=24, max_lon=-67, max_lat=50),
                        temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                        protocol=Protocol.REST,
                        license="CC-BY-NC 4.0",
                        citation="Chaney et al. 2019, POLARIS soil properties",
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

        _, _, layer_id = dataset_id.partition(":")
        parts = layer_id.rsplit("_", 1)
        var_name = parts[0] if len(parts) > 1 else layer_id

        # Map depth label back to code
        depth_label = parts[-1] if len(parts) > 1 else "0-5cm"
        depth_map = dict(zip(DEPTH_LABELS, DEPTHS))
        depth_code = depth_map.get(depth_label, "0_5")

        if var_name not in POLARIS_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_name}")

        var_meta = POLARIS_VARIABLES[var_name]
        bbox = _geometry_to_bbox(geometry)

        # Determine tile from bbox center
        center_lat = (bbox[1] + bbox[3]) / 2
        center_lon = (bbox[0] + bbox[2]) / 2
        tile_lat = int(center_lat) if center_lat >= 0 else int(center_lat) - 1
        tile_lon = int(center_lon) if center_lon >= 0 else int(center_lon) - 1

        tile_url = (
            f"{BASE_URL}/{var_name}/mean/{depth_code}"
            f"/lat{tile_lat}{tile_lat + 1}_lon{tile_lon}{tile_lon + 1}.tif"
        )

        try:
            raster_bytes = await self._get_bytes(tile_url)
        except Exception as e:
            raise DataFormatError(self.slug, f"Tile download failed: {e}") from e

        raster_data, transform, nodata = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=var_name, value=value,
            units=var_meta.units, aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"POLARIS tile: {var_name}/mean/{depth_code}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))
