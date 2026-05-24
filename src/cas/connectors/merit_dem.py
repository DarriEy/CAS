# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MERIT DEM connector — global 90m hydrologically adjusted DEM.

Requires registration at the University of Tokyo MERIT DEM site.
Register: https://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_DEM/
A password will be emailed after filling out the registration form.

Set credentials via environment variables:
  export CAS_MERIT_USER=your_email
  export CAS_MERIT_PASSWORD=your_password
"""

from __future__ import annotations

import os
import time

import structlog

from cas.connectors.base import BaseConnector
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
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

REGISTRATION_URL = "https://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_DEM/"
BASE_URL = "http://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_DEM/distribute/v1.0.3"

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 9000),
    description="Hydrologically adjusted elevation — noise, tree canopy, and speckle removed from SRTM/AW3D",
)


@register("merit_dem")
class MERITDEMConnector(BaseConnector):
    slug = "merit_dem"
    display_name = "MERIT DEM 90m"
    base_url = BASE_URL
    protocol = "rest"

    def _get_credentials(self) -> tuple[str, str]:
        user = self.config.get("user") or os.environ.get("CAS_MERIT_USER", "")
        password = self.config.get("password") or os.environ.get("CAS_MERIT_PASSWORD", "")
        if not user or not password:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Free registration required (University of Tokyo).\n"
                "1. Visit the MERIT DEM page and fill out the registration form\n"
                "2. A download password will be emailed to you\n"
                "3. Set credentials:\n"
                "   export CAS_MERIT_USER=your_email\n"
                "   export CAS_MERIT_PASSWORD=your_password",
            )
        return user, password

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="MERIT DEM 90m Elevation",
                description="Global 90m hydrologically adjusted DEM (noise, canopy, speckle removed)",
                variables=[ELEVATION_VAR],
                resolution_m=90,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="CC-BY-NC 4.0",
                citation="Yamazaki et al. 2017, MERIT DEM",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        user, password = self._get_credentials()
        bbox = _geometry_to_bbox(geometry)

        # MERIT tiles are 5x5 degrees, named by SW corner: e.g. n30w090
        tile_name = _bbox_to_tile(bbox)
        tile_url = f"{BASE_URL}/{tile_name}_dem.tif"

        try:
            import httpx

            async with httpx.AsyncClient(timeout=120.0, auth=(user, password)) as client:
                resp = await client.get(tile_url)
                if resp.status_code == 401:
                    raise RegistrationRequiredError(
                        self.slug,
                        REGISTRATION_URL,
                        "Invalid credentials. Check CAS_MERIT_USER and CAS_MERIT_PASSWORD.",
                    )
                if resp.status_code != 200:
                    raise DataFormatError(self.slug, f"Download failed ({resp.status_code}): {tile_url}")
                raster_bytes = resp.content
        except (RegistrationRequiredError, DataFormatError):
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"Download failed: {e}") from e

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
            provenance=f"MERIT DEM tile: {tile_name}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _bbox_to_tile(bbox: tuple[float, float, float, float]) -> str:
    """Convert bbox center to MERIT DEM tile name (5x5 degree grid)."""
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2
    tile_lat = int(center_lat // 5) * 5
    tile_lon = int(center_lon // 5) * 5
    ns = "n" if tile_lat >= 0 else "s"
    ew = "e" if tile_lon >= 0 else "w"
    return f"{ns}{abs(tile_lat):02d}{ew}{abs(tile_lon):03d}"
