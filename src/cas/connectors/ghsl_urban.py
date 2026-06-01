# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""GHSL — Global Human Settlement Layer: built-up surface & population.

Provider-first: reads JRC's own R2023A 3-arcsec (~100 m) GeoTIFF tiles in
EPSG:4326 directly over ``/vsizip/vsicurl/`` (the tiles ship zipped). This
replaced a Planetary Computer STAC path after Microsoft retired all GHSL
collections from the PC catalog (only ``jrc-gsw`` remains there).

The JRC ``GHS_SMOD`` (settlement degree-of-urbanisation) product has no
3-arcsec tiling — only a 30-arcsec global file (no overviews, ~60 s/read)
and a 1 km Mollweide grid — so it is intentionally not served here.
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

JRC_BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL"

# The R2023A 4326 3-arcsec mosaic is cut into exactly 10x10 degree tiles named
# by row/col (R{r}_C{c}). Its top-left frame origin is offset from the integer
# graticule by a fixed amount (derived from the tile bounds GDAL reports, e.g.
# R5_C19 spans lon [-0.0079, 9.9921], lat [39.0996, 49.0996]). Using these exact
# origins makes the floor-based tile index land on the right tile.
_GHSL_LON0 = -180.0079  # left edge of column 1
_GHSL_LAT0 = 89.0996  # top edge of row 1

GHSL_LAYERS: dict[str, dict] = {
    "built_surface": {
        "product_dir": "GHS_BUILT_S_GLOBE_R2023A",
        "stem": "GHS_BUILT_S_E2020_GLOBE_R2023A_4326_3ss",
        "variable": Variable(
            name="built_surface", units="m2",
            data_type=DataType.CONTINUOUS, valid_range=(0, 10000),
            description="Built-up surface area per grid cell (GHSL-BUILT-S R2023A)",
        ),
        "resolution_m": 100,
    },
    "population": {
        "product_dir": "GHS_POP_GLOBE_R2023A",
        "stem": "GHS_POP_E2020_GLOBE_R2023A_4326_3ss",
        "variable": Variable(
            name="population", units="persons",
            data_type=DataType.CONTINUOUS, valid_range=(0, 1000000),
            description="Population count per grid cell (GHSL-POP R2023A)",
        ),
        "resolution_m": 100,
    },
}


def _ghsl_tile(lon: float, lat: float) -> tuple[int, int]:
    """Return the (row, col) of the R2023A 3ss tile containing ``(lon, lat)``."""
    col = int(math.floor((lon - _GHSL_LON0) / 10.0)) + 1
    row = int(math.floor((_GHSL_LAT0 - lat) / 10.0)) + 1
    return row, col


def _tile_path(product_dir: str, stem: str, row: int, col: int) -> str:
    """Build a ``/vsizip/vsicurl/`` path to a single zipped GHSL tile."""
    tile = f"{stem}_V1_0_R{row}_C{col}"
    return f"/vsizip/vsicurl/{JRC_BASE}/{product_dir}/{stem}/V1-0/tiles/{tile}.zip/{tile}.tif"


@register("ghsl")
class GHSLConnector(STACMixin, BaseConnector):
    slug = "ghsl"
    display_name = "GHSL Global Human Settlement 100m"
    base_url = JRC_BASE
    protocol = "rest"
    # First dataset is built-up surface — ~0 over rural anchors. Pin a dense
    # city core (central Paris) so the health check samples real built surface.
    health_anchor = (2.35, 48.86)

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for ds_key, ds_info in GHSL_LAYERS.items():
            datasets.append(Dataset(
                id=f"{self.slug}:{ds_key}",
                provider=self.slug,
                name=f"GHSL {ds_key.replace('_', ' ').title()}",
                description=ds_info["variable"].description or ds_key,
                variables=[ds_info["variable"]],
                resolution_m=ds_info["resolution_m"],
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.REST,
                license="CC-BY-4.0",
                citation="Pesaresi et al. 2023, JRC GHSL R2023A",
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

        if layer_key not in GHSL_LAYERS:
            raise DataFormatError(self.slug, f"Unknown layer: {layer_key}")

        ds_info = GHSL_LAYERS[layer_key]
        bbox = geometry_to_bbox(geometry)
        center_lon = (bbox[0] + bbox[2]) / 2.0
        center_lat = (bbox[1] + bbox[3]) / 2.0
        row, col = _ghsl_tile(center_lon, center_lat)
        cog_url = _tile_path(ds_info["product_dir"], ds_info["stem"], row, col)

        # The tiles are zipped EPSG:4326 GeoTIFFs on a plain HTTP server. Scope
        # the GDAL/curl options to this read so we don't restrict vsicurl
        # extensions for other connectors that share the process.
        import rasterio

        def _missing(provenance: str) -> AttributeResult:
            return AttributeResult(
                dataset_id=dataset_id, variable=ds_info["variable"].name, value=None,
                units=ds_info["variable"].units, aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=provenance,
            )

        try:
            with rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.zip",
                GDAL_HTTP_MULTIRANGE="YES",
                VSI_CACHE="TRUE",
            ):
                raster_data, transform, nodata, src_crs = await self._read_cog_window(
                    cog_url=cog_url, bbox=bbox, geometry=geometry,
                )
        except Exception as e:
            # Tiles only exist over land; an ocean/polar query hits a 404 (no
            # such tile), which is legitimately "no data here" rather than an
            # outage. Surface it as MISSING.
            logger.debug("ghsl_tile_miss", tile=f"R{row}_C{col}", error=str(e)[:120])
            return _missing(f"GHSL tile R{row}_C{col} unavailable")

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        if coverage == 0.0:
            return _missing(f"GHSL R{row}_C{col}: no valid pixels in geometry")

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL

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
            provenance=f"JRC GHSL R2023A 3ss tile R{row}_C{col}: {ds_info['stem']}",
        )
