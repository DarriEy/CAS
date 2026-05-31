# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ETH Global Canopy Height connector — global 10m canopy height (2020).

Provider: ETH Zurich, direct COG tile download.
Reference: Lang et al. 2023, Nature Ecology & Evolution.
"""

from __future__ import annotations

import math
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
from cas.extract.zonal import compute_zonal_stats, geometry_to_bbox, rasterize_geometry

logger = structlog.get_logger()

ETH_BASE = "https://libdrive.ethz.ch/index.php/s/cO8or7iOe5dT2Rt/download"

CANOPY_HEIGHT_VAR = Variable(
    name="canopy_height", units="m", data_type=DataType.CONTINUOUS,
    valid_range=(0, 60),
    description="Vegetation canopy height from Sentinel-2 imagery",
)


@register("canopy_height")
class CanopyHeightConnector(STACMixin, BaseConnector):
    slug = "canopy_height"
    display_name = "ETH Canopy Height 10m"
    base_url = ETH_BASE
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:canopy_height",
                provider=self.slug,
                name="ETH Global Canopy Height 10m (2020)",
                description="Global 10m vegetation canopy height — controls interception, roughness, snow trapping",
                variables=[CANOPY_HEIGHT_VAR],
                resolution_m=10,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=72),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="CC-BY 4.0",
                citation="Lang et al. 2023, ETH Global Canopy Height",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = geometry_to_bbox(geometry)
        tile_name = _bbox_to_tile(bbox)
        tile_file = f"ETH_GlobalCanopyHeight_10m_2020_{tile_name}_Map.tif"
        tile_url = f"{ETH_BASE}?path=%2F3deg_cogs&files={tile_file}"

        # Each tile is a 36000x36000 (~377MB) COG. Read only the window over the
        # request bbox via /vsicurl range requests instead of downloading the
        # whole tile.
        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                tile_url, bbox, geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"Tile window read failed for {tile_name}: {e}") from e

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
            dataset_id=dataset_id, variable="canopy_height", value=value,
            units="m", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"ETH canopy height tile: {tile_name}",
        )



def _bbox_to_tile(bbox: tuple[float, float, float, float]) -> str:
    """ETH tiles are 3x3 degrees, named by their lower-left corner: e.g. N48E006
    spans 48-51N, 6-9E. Floor the bbox centre to the 3-degree grid."""
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2
    tile_lat = int(math.floor(center_lat / 3) * 3)
    tile_lon = int(math.floor(center_lon / 3) * 3)
    ns = "N" if tile_lat >= 0 else "S"
    ew = "E" if tile_lon >= 0 else "W"
    return f"{ns}{abs(tile_lat):02d}{ew}{abs(tile_lon):03d}"
