# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ESA CCI Land Cover connector — global 300m, annual 1992-2020 via STAC + COG."""

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

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "esa-cci-lc"

CCI_CLASSES = {
    10: "Cropland, rainfed",
    20: "Cropland, irrigated",
    30: "Mosaic cropland/natural vegetation",
    40: "Mosaic natural vegetation/cropland",
    50: "Tree cover, broadleaved, evergreen",
    60: "Tree cover, broadleaved, deciduous",
    70: "Tree cover, needleleaved, evergreen",
    80: "Tree cover, needleleaved, deciduous",
    90: "Tree cover, mixed",
    100: "Mosaic tree and shrub/herbaceous",
    110: "Mosaic herbaceous/tree and shrub",
    120: "Shrubland",
    130: "Grassland",
    140: "Lichens and mosses",
    150: "Sparse vegetation",
    160: "Tree cover, flooded, fresh or brackish",
    170: "Tree cover, flooded, saline",
    180: "Shrub or herbaceous cover, flooded",
    190: "Urban areas",
    200: "Bare areas",
    210: "Water bodies",
    220: "Permanent snow and ice",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="UN-FAO LCCS land cover classification (22 classes)",
)


@register("esa_cci_lc")
class ESACCILandCoverConnector(STACMixin, BaseConnector):
    slug = "esa_cci_lc"
    display_name = "ESA CCI Land Cover 300m"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="ESA CCI Land Cover 300m",
                description="Global 300m annual land cover 1992-2020 (longest continuous time series)",
                variables=[LAND_COVER_VAR],
                resolution_m=300,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="ESA CCI (free)",
                citation="ESA Climate Change Initiative Land Cover, v2.1.1",
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

        datetime_range = "2020-01-01/2020-12-31"
        if time_range:
            datetime_range = f"{time_range.start.isoformat()}/{time_range.end.isoformat()}"

        items = await self._stac_search(
            catalog_url=STAC_URL,
            collections=[COLLECTION],
            bbox=bbox,
            datetime_range=datetime_range,
        )

        if not items:
            return AttributeResult(
                dataset_id=dataset_id,
                variable="land_cover",
                value=None,
                units="class",
                aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC search: {COLLECTION} (no items)",
            )

        item = self._select_best_item(items)
        asset_key = "lccs_class"
        if asset_key not in item.get("assets", {}):
            asset_keys = list(item.get("assets", {}).keys())
            if not asset_keys:
                raise DataFormatError(self.slug, "STAC item has no assets")
            asset_key = asset_keys[0]

        cog_href = item["assets"][asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

        try:
            import planetary_computer

            cog_href = planetary_computer.sign(cog_href)
        except ImportError:
            pass

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {CCI_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', 'unknown')}",
        )
