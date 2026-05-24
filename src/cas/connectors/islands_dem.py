# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""IslandsDEM connector — Iceland 10m elevation via LMI WCS."""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.wcs import WCSMixin
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
    valid_range=(-10, 2200),
)


@register("islands_dem")
class IslandsDEMConnector(WCSMixin, BaseConnector):
    slug = "islands_dem"
    display_name = "IslandsDEM 10m (Iceland)"
    base_url = "https://gis.lmi.is/geoserver/wcs"
    protocol = "wcs"

    COVERAGE_ID = "IslandsDEM:IslandsDEM_v1.0_10x10m"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="IslandsDEM v1.0 10m Elevation",
                description="Iceland 10m DEM from Landmælingar Íslands (National Land Survey of Iceland)",
                variables=[ELEVATION_VAR],
                resolution_m=10,
                crs="EPSG:3057",
                bbox=BoundingBox(min_lon=-24.6, min_lat=63.2, max_lon=-13.1, max_lat=66.6),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.WCS,
                license="Open (LMI)",
                citation="Landmælingar Íslands, IslandsDEM v1.0",
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
            "SUBSETTINGCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
        }

        try:
            resp = await self.client.get("", params=params, timeout=60.0)
            if resp.status_code != 200:
                raise DataFormatError(self.slug, f"WCS returned {resp.status_code}: {resp.text[:200]}")
            raster_bytes = resp.content
        except DataFormatError:
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
