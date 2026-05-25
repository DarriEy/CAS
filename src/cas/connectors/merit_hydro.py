# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MERIT Hydro connector — global 90m hydrography (flow direction, accumulation, width).

Same registration as MERIT DEM (University of Tokyo).
Register: https://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_Hydro/
Set credentials:
  export CAS_MERIT_USER=your_email
  export CAS_MERIT_PASSWORD=your_password
"""

from __future__ import annotations

import os
import time

import httpx
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

REGISTRATION_URL = "https://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_Hydro/"
BASE_URL = "http://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_Hydro/distribute/v1.0.1"

MERIT_HYDRO_VARIABLES: dict[str, tuple[str, Variable]] = {
    "flow_direction": (
        "dir",
        Variable(name="flow_direction", units="D8_code", data_type=DataType.CATEGORICAL,
                 description="D8 flow direction (1=E,2=SE,4=S,8=SW,16=W,32=NW,64=N,128=NE)"),
    ),
    "flow_accumulation": (
        "upa",
        Variable(name="flow_accumulation", units="km2", data_type=DataType.CONTINUOUS,
                 valid_range=(0, 6_200_000), description="Upstream drainage area"),
    ),
    "elevation_adjusted": (
        "elv",
        Variable(name="elevation_adjusted", units="m", data_type=DataType.CONTINUOUS,
                 valid_range=(-500, 9000), description="Hydrologically adjusted elevation"),
    ),
    "river_width": (
        "wth",
        Variable(name="river_width", units="m", data_type=DataType.CONTINUOUS,
                 valid_range=(0, 50000), description="River channel width"),
    ),
}


@register("merit_hydro")
class MERITHydroConnector(BaseConnector):
    slug = "merit_hydro"
    display_name = "MERIT Hydro 90m"
    base_url = BASE_URL
    protocol = "rest"

    def _get_credentials(self) -> tuple[str, str]:
        user = self.config.get("user") or os.environ.get("CAS_MERIT_USER", "")
        password = self.config.get("password") or os.environ.get("CAS_MERIT_PASSWORD", "")
        if not user or not password:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Free registration required (same as MERIT DEM).\n"
                "1. Visit the MERIT Hydro page and fill out the registration form\n"
                "2. A download password will be emailed to you\n"
                "3. Set credentials:\n"
                "   export CAS_MERIT_USER=your_email\n"
                "   export CAS_MERIT_PASSWORD=your_password",
            )
        return user, password

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, (_, var_meta) in MERIT_HYDRO_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"MERIT Hydro {var_meta.description or var_meta.name}",
                    description=f"Global 90m {var_meta.description} — hydrological routing attribute",
                    variables=[var_meta],
                    resolution_m=90,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=90),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.REST,
                    license="CC-BY-NC 4.0 / ODbL 1.0",
                    citation="Yamazaki et al. 2019, MERIT Hydro",
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
        user, password = self._get_credentials()

        _, _, var_key = dataset_id.partition(":")
        if var_key not in MERIT_HYDRO_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        file_prefix, var_meta = MERIT_HYDRO_VARIABLES[var_key]
        bbox = _geometry_to_bbox(geometry)
        tile_name = _bbox_to_tile(bbox)

        tile_url = f"{BASE_URL}/{file_prefix}/{tile_name}{file_prefix}.tif"

        try:
            async with httpx.AsyncClient(timeout=120.0, auth=(user, password)) as client:
                resp = await client.get(tile_url)
                if resp.status_code == 401:
                    raise RegistrationRequiredError(
                        self.slug, REGISTRATION_URL,
                        "Invalid credentials. Check CAS_MERIT_USER and CAS_MERIT_PASSWORD.",
                    )
                if resp.status_code != 200:
                    raise DataFormatError(self.slug, f"Download failed ({resp.status_code}): {tile_url}")
                raster_bytes = resp.content
        except (RegistrationRequiredError, DataFormatError):
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"Download failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
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
            dataset_id=dataset_id, variable=var_meta.name, value=value,
            units=var_meta.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"MERIT Hydro tile: {file_prefix}/{tile_name}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Point":
        lon, lat = geometry.coordinates[0], geometry.coordinates[1]
        buf = 0.001
        return (lon - buf, lat - buf, lon + buf, lat + buf)
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _bbox_to_tile(bbox: tuple[float, float, float, float]) -> str:
    """MERIT Hydro uses 5x5 degree tiles named by SW corner."""
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2
    tile_lat = int(center_lat // 5) * 5
    tile_lon = int(center_lon // 5) * 5
    ns = "n" if tile_lat >= 0 else "s"
    ew = "e" if tile_lon >= 0 else "w"
    return f"{ns}{abs(tile_lat):02d}{ew}{abs(tile_lon):03d}"
