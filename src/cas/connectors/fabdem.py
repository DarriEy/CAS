# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""FABDEM connector — global 30m forest/building-removed DEM.

FABDEM is freely downloadable (no registration for research use).
Data hosted on University of Bristol data repository.
Commercial use requires contacting fabdem@fathom.global.

Can also use the `fabdem` Python package: pip install fabdem
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

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 9000),
    description="Copernicus DEM with forest canopy and building heights removed",
)

# FABDEM tiles are 1x1 degree within 10x10 degree ZIP archives
# Direct tile access via the University of Bristol data repo
BRISTOL_BASE = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn"


@register("fabdem")
class FABDEMConnector(BaseConnector):
    slug = "fabdem"
    display_name = "FABDEM 30m"
    base_url = BRISTOL_BASE
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="FABDEM V1-2 30m Elevation",
                description="Global 30m DEM with forest and building heights removed (Copernicus-based)",
                variables=[ELEVATION_VAR],
                resolution_m=30,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="CC-BY-NC-SA 4.0 (commercial: contact fabdem@fathom.global)",
                citation="Hawker et al. 2022, FABDEM V1-2",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = _geometry_to_bbox(geometry)
        tile_name = _bbox_to_tile_name(bbox)

        tile_url = f"{BRISTOL_BASE}/{tile_name}_FABDEM_V1-2.tif"

        try:
            raster_bytes = await self._get_bytes(tile_url)
        except Exception as e:
            raise DataFormatError(
                self.slug,
                f"FABDEM tile download failed for {tile_name}: {e}. "
                f"Alternative: pip install fabdem && fabdem --help",
            ) from e

        raster_data, transform, nodata = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform)

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
            variable="elevation",
            value=value,
            units="m",
            aggregation=AggregationMethod.MEAN,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"FABDEM tile: {tile_name}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _bbox_to_tile_name(bbox: tuple[float, float, float, float]) -> str:
    """Convert bbox center to FABDEM 1x1 degree tile name (SW corner convention)."""
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2
    tile_lat = int(center_lat) if center_lat >= 0 else int(center_lat) - 1
    tile_lon = int(center_lon) if center_lon >= 0 else int(center_lon) - 1
    ns = "N" if tile_lat >= 0 else "S"
    ew = "E" if tile_lon >= 0 else "W"
    return f"{ns}{abs(tile_lat):02d}{ew}{abs(tile_lon):03d}"
