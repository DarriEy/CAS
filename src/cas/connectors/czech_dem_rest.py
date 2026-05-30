# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Czech Republic DMR5G connector — 5m DEM via CUZK ArcGIS REST ImageServer."""

from __future__ import annotations

import time

import httpx
import structlog

from cas.connectors.base import BaseConnector
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
from cas.extract.zonal import compute_zonal_stats, geometry_to_bbox, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

IMAGE_SERVER = "https://ags.cuzk.cz/arcgis2/rest/services/dmr5g/ImageServer"

ELEV_VAR = Variable(
    name="elevation", units="m", data_type=DataType.CONTINUOUS,
    valid_range=(-500, 2000),
)


@register("czech_dem_rest")
class CzechDEMRESTConnector(BaseConnector):
    slug = "czech_dem_rest"
    display_name = "Czech Republic DMR5G 5m (REST)"
    base_url = IMAGE_SERVER
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:elevation",
                provider=self.slug,
                name="Czech Republic DMR5G 5m (CUZK ImageServer)",
                description="Czech 5m LiDAR DEM via ArcGIS REST with server-side clip",
                variables=[ELEV_VAR],
                resolution_m=5,
                crs="EPSG:5514",
                bbox=BoundingBox(min_lon=12.1, min_lat=48.6, max_lon=18.9, max_lat=51.1),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="Open (CUZK)",
                citation="CUZK, Digitální model reliéfu 5. generace",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = geometry_to_bbox(geometry)

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(
                    f"{IMAGE_SERVER}/exportImage",
                    params={
                        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                        "bboxSR": "4326",
                        "imageSR": "4326",
                        "size": "500,500",
                        "format": "tiff",
                        "f": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                href = data.get("href")
                if not href:
                    raise DataFormatError(self.slug, "No href in response")

                tiff_resp = await client.get(href, timeout=30)
                tiff_resp.raise_for_status()
                raster_bytes = tiff_resp.content

        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"ImageServer failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="elevation", value=value,
            units="m", aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance="CUZK DMR5G ImageServer",
        )

