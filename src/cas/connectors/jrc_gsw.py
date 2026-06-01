# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""JRC Global Surface Water connector — 30m water dynamics from 1984-2021.

Provider-first: direct GeoTIFF tiles from Google Cloud Storage (no auth).
Fallback: Planetary Computer STAC+COG.

Layers: occurrence, change, seasonality, recurrence, extent.
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
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

GCS_BASE = "https://storage.googleapis.com/global-surface-water/downloads2021"
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_COLLECTION = "jrc-gsw"

GSW_LAYERS: dict[str, Variable] = {
    "occurrence": Variable(
        name="occurrence", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
        description="Frequency of water presence 1984-2021",
    ),
    "change": Variable(
        name="change", units="delta_%", data_type=DataType.CONTINUOUS, valid_range=(-100, 100),
        description="Change in water occurrence between periods",
    ),
    "seasonality": Variable(
        name="seasonality", units="months", data_type=DataType.CONTINUOUS, valid_range=(0, 12),
        description="Number of months water is present per year",
    ),
    "recurrence": Variable(
        name="recurrence", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
        description="Frequency of inter-annual water recurrence",
    ),
    "extent": Variable(
        name="extent", units="class", data_type=DataType.CATEGORICAL,
        description="Maximum water extent classification",
    ),
}


@register("jrc_gsw")
class JRCGlobalSurfaceWaterConnector(STACMixin, BaseConnector):
    slug = "jrc_gsw"
    display_name = "JRC Global Surface Water"
    base_url = GCS_BASE
    protocol = "rest"
    # First dataset is water "occurrence" — over a random land anchor it is
    # legitimately 0/nodata. Pin a permanent water body (Lake Victoria) so the
    # health check exercises a place this layer actually has water.
    health_anchor = (33.0, -1.5)

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for layer_name, var_meta in GSW_LAYERS.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{layer_name}",
                    provider=self.slug,
                    name=f"JRC GSW {layer_name.title()}",
                    description=f"Global 30m surface water {layer_name} (1984-2021, Landsat-derived)",
                    variables=[var_meta],
                    resolution_m=30,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=78),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.REST,
                    license="EC JRC (free)",
                    citation="Pekel et al. 2016, JRC Global Surface Water",
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

        _, _, layer_name = dataset_id.partition(":")
        if layer_name not in GSW_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_name}")

        var_meta = GSW_LAYERS[layer_name]
        bbox = self._geometry_to_bbox(geometry)

        # Try provider first (GCS direct tiles), then Planetary Computer fallback
        raster_data = None
        transform = None
        nodata = None
        provenance = ""

        try:
            tile_url = _build_gcs_tile_url(layer_name, bbox)
            raster_bytes = await self._get_bytes(tile_url)
            raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
            provenance = f"JRC GCS tile: {layer_name}"
            logger.debug("jrc_gsw_provider_hit", layer=layer_name, source="gcs")
        except Exception as gcs_err:
            logger.debug("jrc_gsw_provider_miss", layer=layer_name, error=str(gcs_err)[:100])

            # Fallback to Planetary Computer
            try:
                items = await self._stac_search(
                    catalog_url=PC_STAC_URL, collections=[PC_COLLECTION], bbox=bbox,
                )
                if items:
                    item = self._select_best_item(items)
                    asset_key = layer_name
                    if asset_key not in item.get("assets", {}):
                        asset_key = list(item.get("assets", {}).keys())[0]
                    cog_href = item["assets"][asset_key]["href"]
                    cog_href = self._sign_planetary_computer(cog_href)
                    raster_data, transform, nodata, src_crs = await self._read_cog_window(
                        cog_url=cog_href, bbox=bbox, geometry=geometry,
                    )
                    provenance = f"PC STAC fallback: {PC_COLLECTION}/{item.get('id', '')}"
                    logger.debug("jrc_gsw_fallback_hit", layer=layer_name, source="planetary_computer")
            except Exception as pc_err:
                raise DataFormatError(
                    self.slug,
                    f"Both provider (GCS: {gcs_err}) and fallback (PC: {pc_err}) failed",
                ) from pc_err

        if raster_data is None:
            return AttributeResult(
                dataset_id=dataset_id, variable=layer_name, value=None,
                units=var_meta.units, aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="No data from provider or fallback",
            )

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        agg = AggregationMethod.DISTRIBUTION if var_meta.data_type == DataType.CATEGORICAL else AggregationMethod.MEAN
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=var_meta.data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=layer_name, value=value,
            units=var_meta.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms, provenance=provenance,
        )


def _build_gcs_tile_url(layer: str, bbox: tuple[float, float, float, float]) -> str:
    """Build GCS tile URL from bbox center. Tiles are 10x10 degree."""
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2

    # JRC GSW tiles are 10x10 deg, named by their TOP-LEFT corner. Longitude is
    # the left edge (floor); latitude is the TOP edge, so a point at lat Y sits in
    # the tile whose top = ceil(Y/10)*10 (e.g. -1.5 -> 0N, not 10S). Using floor
    # for latitude fetched the tile one band too far south, missing the point.
    tile_lon = int(math.floor(center_lon / 10.0)) * 10
    tile_lat = int(math.ceil(center_lat / 10.0)) * 10

    lon_str = f"{abs(tile_lon)}E" if tile_lon >= 0 else f"{abs(tile_lon)}W"
    lat_str = f"{abs(tile_lat)}N" if tile_lat >= 0 else f"{abs(tile_lat)}S"

    return f"{GCS_BASE}/{layer}/{layer}_{lon_str}_{lat_str}v1_4_2021.tif"
