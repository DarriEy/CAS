# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""TanDEM-X 90m connector — global 90m DEM from DLR via WCS.

Requires free registration at DLR:
  https://sso.eoc.dlr.de/pwm-tdmdem90
After registration, set credentials:
  export CAS_TANDEMX_USER=your_email
  export CAS_TANDEMX_PASSWORD=your_password
"""

from __future__ import annotations

import os
import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.wcs import WCSMixin
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

REGISTRATION_URL = "https://sso.eoc.dlr.de/pwm-tdmdem90"

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 9000),
    description="Ellipsoidal height (WGS84) from TanDEM-X radar interferometry",
)


@register("tandem_x")
class TanDEMXConnector(WCSMixin, BaseConnector):
    slug = "tandem_x"
    display_name = "TanDEM-X 90m (DLR)"
    base_url = "https://geoservice.dlr.de/eoc/elevation/wcs"
    protocol = "wcs"

    COVERAGE_ID = "TDM_DEM__DEM"

    def _get_credentials(self) -> tuple[str, str]:
        user = self.config.get("user") or os.environ.get("CAS_TANDEMX_USER", "")
        password = self.config.get("password") or os.environ.get("CAS_TANDEMX_PASSWORD", "")
        if not user or not password:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Free registration required (DLR).\n"
                "1. Register at: https://sso.eoc.dlr.de/pwm-tdmdem90\n"
                "2. Set credentials:\n"
                "   export CAS_TANDEMX_USER=your_email\n"
                "   export CAS_TANDEMX_PASSWORD=your_password",
            )
        return user, password

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="TanDEM-X 90m Elevation",
                description="Global 90m DEM from TanDEM-X radar (DLR). Ellipsoidal heights (WGS84).",
                variables=[ELEVATION_VAR],
                resolution_m=90,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.WCS,
                license="DLR (free for scientific use)",
                citation="DLR, TanDEM-X 90m DEM",
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
        bbox = self._geometry_to_bbox(geometry)

        import httpx

        params = {
            "service": "WCS",
            "version": "2.0.1",
            "request": "GetCoverage",
            "CoverageId": self.COVERAGE_ID,
            "format": "image/tiff",
            "subset": [
                f"Long({bbox[0]},{bbox[2]})",
                f"Lat({bbox[1]},{bbox[3]})",
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=120.0, auth=(user, password)) as client:
                resp = await client.get(self.base_url, params=params)
                if resp.status_code == 401:
                    raise RegistrationRequiredError(
                        self.slug,
                        REGISTRATION_URL,
                        "Invalid credentials. Check CAS_TANDEMX_USER and CAS_TANDEMX_PASSWORD.",
                    )
                if resp.status_code != 200:
                    raise DataFormatError(self.slug, f"WCS returned {resp.status_code}")
                raster_bytes = resp.content
        except (RegistrationRequiredError, DataFormatError):
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"WCS request failed: {e}") from e

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
            provenance=f"WCS GetCoverage: {self.COVERAGE_ID}",
        )
