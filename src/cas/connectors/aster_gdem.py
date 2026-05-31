# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ASTER GDEM v3 connector — global 30m elevation via NASA CMR STAC + COG.

The DEM rasters live in NASA's ``lp-prod-protected`` bucket and require a free
Earthdata login: a tokenless request 401s and redirects to urs.earthdata.nasa.gov.
This connector therefore gates on an Earthdata bearer token and raises
``RegistrationRequiredError`` up front when it is unset (so health classifies it
auth-gated/UNKNOWN, not down). Get a token at
https://urs.earthdata.nasa.gov/users/tokens then:
  export CAS_EARTHDATA_TOKEN=your_token
"""

from __future__ import annotations

import os
import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.stac import STACMixin
from cas.core.exceptions import DataFormatError, RegistrationRequiredError
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

STAC_URL = "https://cmr.earthdata.nasa.gov/stac/LPCLOUD"
# STAC collection id uses an underscore (the ``ASTGTM.003`` form is the dataset
# short-name + version and returns zero items from the STAC /search endpoint).
COLLECTION = "ASTGTM_003"

REGISTRATION_URL = "https://urs.earthdata.nasa.gov/users/tokens"
EARTHDATA_TOKEN_ENV = "CAS_EARTHDATA_TOKEN"
_REGISTRATION_INSTRUCTIONS = (
    "Free NASA Earthdata login required (ASTER GDEM tiles are in the "
    "lp-prod-protected bucket).\n"
    "1. Register at: https://urs.earthdata.nasa.gov/users/new\n"
    "2. Generate a token at: https://urs.earthdata.nasa.gov/users/tokens\n"
    "3. Set it:\n"
    f"   export {EARTHDATA_TOKEN_ENV}=your_token"
)

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 9000),
)


@register("aster_gdem")
class ASTERGDEMConnector(STACMixin, BaseConnector):
    slug = "aster_gdem"
    display_name = "ASTER GDEM v3 30m"
    base_url = STAC_URL
    protocol = "stac_cog"

    def _get_token(self) -> str:
        token = self.config.get("earthdata_token") or os.environ.get(EARTHDATA_TOKEN_ENV, "")
        if not token:
            raise RegistrationRequiredError(
                self.slug, REGISTRATION_URL, _REGISTRATION_INSTRUCTIONS
            )
        return str(token)

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="ASTER GDEM v3 30m Elevation",
                description=(
                    "ASTER Global DEM v3 30m — stereo-derived elevation "
                    "from ASTER imagery (83N to 83S)"
                ),
                variables=[ELEVATION_VAR],
                resolution_m=30,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-83, max_lon=180, max_lat=83),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.STAC_COG,
                license="Open (NASA Earthdata login required)",
                citation="NASA/METI, ASTER GDEM v3",
            )
        ]

    def _read_dem_window(
        self,
        cog_url: str,
        bbox: tuple[float, float, float, float],
        token: str,
    ):
        """Read a window from an Earthdata-protected COG with a bearer token.

        STACMixin._read_cog_window can't carry auth; the protected tiles need an
        Authorization header, passed to GDAL/curl via GDAL_HTTP_HEADERS.
        """
        import rasterio
        from rasterio.windows import from_bounds

        gdal_env = {
            "GDAL_HTTP_HEADERS": f"Authorization: Bearer {token}",
            "GDAL_HTTP_TIMEOUT": "60",
            "GDAL_HTTP_MAX_RETRY": "3",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "VSI_CACHE": "TRUE",
        }
        vsi_url = cog_url if cog_url.startswith("/vsicurl/") else f"/vsicurl/{cog_url}"
        with rasterio.Env(**gdal_env), rasterio.open(vsi_url) as src:
            window = from_bounds(*bbox, transform=src.transform)
            arr = src.read(1, window=window)
            transform = src.window_transform(window)
            nodata = src.nodata
            src_crs = src.crs
        return arr, transform, nodata, src_crs

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        # Gate up front: an unset token classifies as auth-gated (UNKNOWN), not down.
        token = self._get_token()
        bbox = self._geometry_to_bbox(geometry)

        items = await self._stac_search(
            catalog_url=STAC_URL,
            collections=[COLLECTION],
            bbox=bbox,
        )

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
        assets = item.get("assets", {})
        # The DEM asset key is dynamic (e.g. "003/ASTGTMV003_N46E008_dem"); it
        # ends in "_dem". A "_num" asset (quality count) sits alongside it.
        asset_key = ""
        for key in assets:
            if key.lower().endswith("_dem"):
                asset_key = key
                break
        if not asset_key:
            for candidate in ("ASTER_GDEM_DEM", "dem", "elevation", "data"):
                if candidate in assets:
                    asset_key = candidate
                    break
        if not asset_key:
            raise DataFormatError(self.slug, "STAC item has no DEM asset")

        cog_href = assets[asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

        try:
            raster_data, transform, nodata, src_crs = self._read_dem_window(
                cog_url=cog_href,
                bbox=bbox,
                token=token,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

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
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
