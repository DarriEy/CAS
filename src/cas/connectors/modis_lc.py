# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MODIS MCD12Q1 connector — global 500m annual land cover via AppEEARS.

Requires free NASA Earthdata login:
  https://urs.earthdata.nasa.gov/users/new
Set credentials:
  export CAS_EARTHDATA_USER=your_username
  export CAS_EARTHDATA_PASSWORD=your_password
"""

from __future__ import annotations

import asyncio
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

REGISTRATION_URL = "https://urs.earthdata.nasa.gov/users/new"

IGBP_CLASSES = {
    1: "Evergreen needleleaf forests",
    2: "Evergreen broadleaf forests",
    3: "Deciduous needleleaf forests",
    4: "Deciduous broadleaf forests",
    5: "Mixed forests",
    6: "Closed shrublands",
    7: "Open shrublands",
    8: "Woody savannas",
    9: "Savannas",
    10: "Grasslands",
    11: "Permanent wetlands",
    12: "Croplands",
    13: "Urban and built-up lands",
    14: "Cropland/natural vegetation mosaics",
    15: "Permanent snow and ice",
    16: "Barren",
    17: "Water bodies",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="IGBP land cover classification (17 classes)",
)


@register("modis_lc")
class MODISLandCoverConnector(BaseConnector):
    slug = "modis_lc"
    display_name = "MODIS MCD12Q1 500m"
    base_url = "https://appeears.earthdatacloud.nasa.gov/api"
    protocol = "rest"

    def _get_credentials(self) -> tuple[str, str]:
        user = self.config.get("user") or os.environ.get("CAS_EARTHDATA_USER", "")
        password = self.config.get("password") or os.environ.get("CAS_EARTHDATA_PASSWORD", "")
        if not user or not password:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Free NASA Earthdata login required.\n"
                "1. Register at: https://urs.earthdata.nasa.gov/users/new\n"
                "2. Set credentials:\n"
                "   export CAS_EARTHDATA_USER=your_username\n"
                "   export CAS_EARTHDATA_PASSWORD=your_password",
            )
        return user, password

    async def _get_token(self) -> str:
        user, password = self._get_credentials()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/login",
                auth=(user, password),
            )
            if resp.status_code == 401:
                raise RegistrationRequiredError(
                    self.slug, REGISTRATION_URL,
                    "Invalid Earthdata credentials.",
                )
            resp.raise_for_status()
            return str(resp.json()["token"])

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="MODIS MCD12Q1 500m Land Cover",
                description="Global 500m annual land cover (IGBP, 2001-present)",
                variables=[LAND_COVER_VAR],
                resolution_m=500,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.REST,
                license="Open (NASA)",
                citation="Friedl et al., MODIS MCD12Q1 v6.1",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()

        token = await self._get_token()
        bbox = _geometry_to_bbox(geometry)

        year = 2022
        if time_range:
            year = time_range.start.year

        # AppEEARS area request (async job)
        task_payload = {
            "task_type": "area",
            "task_name": f"cas_modis_lc_{year}",
            "params": {
                "dates": [{"startDate": f"01-01-{year}", "endDate": f"12-31-{year}"}],
                "layers": [{"product": "MCD12Q1.061", "layer": "LC_Type1"}],
                "output": {"format": {"type": "geotiff"}, "projection": "geographic"},
                "geo": {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[bbox[0], bbox[1]], [bbox[2], bbox[1]],
                                 [bbox[2], bbox[3]], [bbox[0], bbox[3]],
                                 [bbox[0], bbox[1]]]
                            ],
                        },
                        "properties": {},
                    }],
                },
            },
        }

        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                # Submit task
                resp = await client.post(
                    f"{self.base_url}/task", json=task_payload, headers=headers,
                )
                resp.raise_for_status()
                task_id = resp.json()["task_id"]

                # Poll for completion (max 2 minutes)
                for _ in range(24):
                    await asyncio.sleep(5)
                    status_resp = await client.get(
                        f"{self.base_url}/task/{task_id}", headers=headers,
                    )
                    status = status_resp.json().get("status", "")
                    if status == "done":
                        break
                    if status == "error":
                        raise DataFormatError(self.slug, "AppEEARS task failed")
                else:
                    raise DataFormatError(self.slug, "AppEEARS task timed out")

                # Get file list and download
                bundle_resp = await client.get(
                    f"{self.base_url}/bundle/{task_id}", headers=headers,
                )
                files = bundle_resp.json().get("files", [])
                tiff_files = [f for f in files if f["file_name"].endswith(".tif")]
                if not tiff_files:
                    raise DataFormatError(self.slug, "No GeoTIFF in AppEEARS result")

                file_id = tiff_files[0]["file_id"]
                dl_resp = await client.get(
                    f"{self.base_url}/bundle/{task_id}/{file_id}",
                    headers=headers, timeout=60,
                )
                raster_bytes = dl_resp.content

        except (RegistrationRequiredError, DataFormatError):
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"AppEEARS request failed: {e}") from e

        raster_data, transform, nodata = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {IGBP_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"AppEEARS MCD12Q1: {year}",
        )


def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
    if geometry.type == "Polygon":
        coords = geometry.coordinates[0]
    else:
        coords = [c for ring in geometry.coordinates for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))
