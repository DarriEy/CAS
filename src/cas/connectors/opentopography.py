# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""OpenTopography connector — server-side DEM subsetting via REST API.

Provides access to SRTM (30m/90m), COP30, COP90, NASADEM, AW3D30, EU_DTM,
and more via a single API with bbox-based server-side subsetting.

Requires a free API key: https://portal.opentopography.org/ (My Account → API Keys)
Set via CAS_OPENTOPOGRAPHY_API_KEY env var or config.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

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
from cas.extract.zonal import compute_zonal_stats, geometry_to_bbox, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

REGISTRATION_URL = "https://portal.opentopography.org/"

ELEVATION_VAR = Variable(
    name="elevation",
    units="m",
    data_type=DataType.CONTINUOUS,
    valid_range=(-500, 9000),
)

@dataclass
class DEMType:
    name: str
    resolution_m: float
    bbox: BoundingBox


DEMTYPES: dict[str, DEMType] = {
    "SRTMGL1": DEMType("SRTM GL1 30m", 30, BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=60)),
    "SRTMGL3": DEMType("SRTM GL3 90m", 90, BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=60)),
    "COP30": DEMType("Copernicus DEM 30m", 30, BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)),
    "COP90": DEMType("Copernicus DEM 90m", 90, BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)),
    "NASADEM": DEMType("NASADEM 30m", 30, BoundingBox(min_lon=-180, min_lat=-56, max_lon=180, max_lat=60)),
    "AW3D30": DEMType("ALOS World 3D 30m", 30, BoundingBox(min_lon=-180, min_lat=-60, max_lon=180, max_lat=82)),
    "EU_DTM": DEMType("EU DTM 30m", 30, BoundingBox(min_lon=-25, min_lat=34, max_lon=45, max_lat=72)),
}


@register("opentopography")
class OpenTopographyConnector(BaseConnector):
    slug = "opentopography"
    display_name = "OpenTopography"
    base_url = "https://portal.opentopography.org/API"
    protocol = "rest"

    def _get_api_key(self) -> str:
        key = self.config.get("api_key") or os.environ.get("CAS_OPENTOPOGRAPHY_API_KEY", "")
        if not key:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Free API key required. Register at OpenTopography, then:\n"
                "  export CAS_OPENTOPOGRAPHY_API_KEY=your_key\n"
                "Or pass config={'api_key': 'your_key'} to the connector.",
            )
        return key

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for demtype, meta in DEMTYPES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{demtype}",
                    provider=self.slug,
                    name=f"OpenTopography {meta.name}",
                    description=f"{meta.name} via OpenTopography server-side subsetting",
                    variables=[ELEVATION_VAR],
                    resolution_m=meta.resolution_m,
                    crs="EPSG:4326",
                    bbox=meta.bbox,
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.REST,
                    license="Varies by dataset",
                    citation="OpenTopography Facility",
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
        api_key = self._get_api_key()

        _, _, demtype = dataset_id.partition(":")
        if demtype not in DEMTYPES:
            raise DataFormatError(self.slug, f"Unknown DEM type: {demtype}. Available: {list(DEMTYPES.keys())}")

        bbox = geometry_to_bbox(geometry)

        try:
            resp = await self.client.get(
                "/globaldem",
                params={
                    "demtype": demtype,
                    "south": bbox[1],
                    "north": bbox[3],
                    "west": bbox[0],
                    "east": bbox[2],
                    "outputFormat": "GTiff",
                    "API_Key": api_key,
                },
                timeout=120.0,
            )
            if resp.status_code != 200:
                raise DataFormatError(self.slug, f"API returned {resp.status_code}: {resp.text[:200]}")
            raster_bytes = resp.content
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"API request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
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
            provenance=f"OpenTopography API: {demtype}",
        )

