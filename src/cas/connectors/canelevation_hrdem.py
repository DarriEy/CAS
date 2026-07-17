# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Canada HRDEM connector — 1m lidar elevation via the CanElevation STAC + public COGs.

Replaces the former WMS-mosaic connector, which pointed at a *visualisation* endpoint
(``datacube.services.geo.ca/ows/elevation``) that returned rendered, unusable int32 data
(stats came back ~-1.2e9, flagged ``suspect``). The real data lives in the NRCan
CanElevation STAC (``datacube.services.geo.ca/api``) as public, anonymously-readable
Cloud-Optimized GeoTIFFs on S3 — the same STAC+COG path proven by ``usgs_3dep``.

Serves the **DTM** (bare-earth terrain) by default, which is what hydraulic/flood models
want; the DSM (surface, incl. buildings/canopy) is available as an alternate asset.
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

# Direct STAC API endpoint. NB: /api/search 308-redirects here, and the STAC mixin's
# POST search does not follow redirects — so target /stac/api directly.
STAC_URL = "https://datacube.services.geo.ca/stac/api"
COLLECTION = "hrdem-mosaic-1m"  # 1m lidar mosaic; hrdem-mosaic-2m also available

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 9000),
)


@register("canada_hrdem")
class CanElevationHRDEMConnector(STACMixin, BaseConnector):
    slug = "canada_hrdem"
    display_name = "Canada HRDEM 1m (CanElevation)"
    base_url = STAC_URL
    protocol = "stac_cog"

    # In-process raster mode: all-intersecting-tile windowed mosaic via STACMixin.
    # Public S3 COGs need no Planetary-Computer signing.
    supports_raster = True
    stac_raster_collections = (COLLECTION,)
    stac_raster_asset = "dtm"  # bare-earth terrain (DSM available as "dsm")
    stac_sign_assets = False

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="Canada HRDEM 1m Elevation (DTM)",
                description="Canadian High Resolution DEM, 1m lidar bare-earth terrain (CanElevation)",
                variables=[ELEVATION_VAR],
                resolution_m=1,
                crs="EPSG:3979",
                bbox=BoundingBox(min_lon=-141, min_lat=41.7, max_lon=-52.6, max_lat=83.1),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="Open Government Licence - Canada",
                citation="Natural Resources Canada, CanElevation HRDEM",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)

        items = await self._stac_search(catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox)
        if not items:
            return AttributeResult(
                dataset_id=dataset_id,
                variable="elevation",
                value=None,
                units="m",
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items found)",
            )

        item = self._select_best_item(items)
        cog_href = self._raster_asset_href(item)  # "dtm" asset, unsigned public COG

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        mask = rasterize_geometry(geometry.model_dump(), raster_data.shape, transform, src_crs)
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data,
            mask=mask,
            nodata=nodata,
            aggregation=AggregationMethod.MEAN,
            data_type=DataType.CONTINUOUS,
        )

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
            elapsed_ms=int((time.monotonic() - start_time) * 1000),
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')} (DTM)",
        )
