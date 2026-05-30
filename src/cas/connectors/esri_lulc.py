# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Esri 10m LULC connector — global annual land cover via ArcGIS ImageServer.

Provider-first: Esri ArcGIS Living Atlas ImageServer (exportImage with server-side clip).
Fallback: Planetary Computer STAC+COG.
"""

from __future__ import annotations

import time

import httpx
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
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

ESRI_IMAGESERVER = "https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer"
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_COLLECTION = "io-lulc-annual-v02"

ESRI_CLASSES = {
    1: "No data",
    2: "Water",
    3: "Trees",
    4: "Flooded vegetation",
    5: "Crops",
    6: "Scrub/shrub",
    7: "Built area",
    8: "Bare ground",
    9: "Snow/ice",
    10: "Clouds",
    11: "Rangeland",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Sentinel-2 derived 9-class LULC (2017-2025)",
)


@register("esri_lulc")
class EsriLULCConnector(STACMixin, BaseConnector):
    slug = "esri_lulc"
    display_name = "Esri 10m LULC"
    base_url = ESRI_IMAGESERVER
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="Esri 10m Annual Land Cover",
                description="Global 10m annual LULC from Sentinel-2 (9 classes, 2017-2025)",
                variables=[LAND_COVER_VAR],
                resolution_m=10,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.REST,
                license="CC-BY 4.0",
                citation="Impact Observatory / Esri, Sentinel-2 10m LULC",
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

        raster_data = None
        transform = None
        nodata = None
        src_crs = "EPSG:4326"
        provenance = ""

        # Provider first: Esri ImageServer exportImage
        try:
            raster_data, transform, nodata, provenance = await self._extract_from_esri(bbox)
            logger.debug("esri_lulc_provider_hit", source="imageserver")
        except Exception as esri_err:
            logger.debug("esri_lulc_provider_miss", error=str(esri_err)[:100])

            # Fallback: Planetary Computer
            try:
                items = await self._stac_search(
                    catalog_url=PC_STAC_URL, collections=[PC_COLLECTION], bbox=bbox,
                )
                if items:
                    item = self._select_best_item(items)
                    asset_key = "data"
                    if asset_key not in item.get("assets", {}):
                        asset_key = list(item.get("assets", {}).keys())[0]
                    cog_href = item["assets"][asset_key]["href"]
                    cog_href = self._sign_planetary_computer(cog_href)
                    raster_data, transform, nodata, src_crs = await self._read_cog_window(
                        cog_url=cog_href, bbox=bbox, geometry=geometry,
                    )
                    provenance = f"PC STAC fallback: {PC_COLLECTION}/{item.get('id', '')}"
            except Exception as pc_err:
                raise DataFormatError(
                    self.slug,
                    f"Both provider (Esri: {esri_err}) and fallback (PC: {pc_err}) failed",
                ) from pc_err

        if raster_data is None:
            return AttributeResult(
                dataset_id=dataset_id, variable="land_cover", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="No data",
            )

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {ESRI_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms, provenance=provenance,
        )

    async def _extract_from_esri(self, bbox: tuple[float, float, float, float]):
        """Server-side export from Esri ImageServer."""
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(
                f"{ESRI_IMAGESERVER}/exportImage",
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
                raise DataFormatError(self.slug, "No href in ImageServer response")

            tiff_resp = await client.get(href)
            tiff_resp.raise_for_status()
            raster_data, transform, nodata, src_crs = parse_geotiff(tiff_resp.content)
            return raster_data, transform, nodata, "Esri ImageServer exportImage"
