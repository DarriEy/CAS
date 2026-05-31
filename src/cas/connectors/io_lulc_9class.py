# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Impact Observatory 10m Annual LULC (9-class V1) via Planetary Computer.

This is the original 9-class Impact Observatory / Esri product
(``io-lulc-9-class`` collection on the Planetary Computer), distinct from the
newer ``io-lulc-annual-v02`` mosaic exposed by the ``io_lulc`` connector. The
9-class V1 layers use a different code scheme and remain the reference product
for several downstream comparisons, so we expose them as their own connector
following the canonical STAC + COG categorical pattern.
"""

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
COLLECTION = "io-lulc-9-class"

# io-lulc-9-class pixel value → class name (file:values on the "data" asset).
IO9_CLASSES = {
    0: "No data",
    1: "Water",
    2: "Trees",
    4: "Flooded vegetation",
    5: "Crops",
    7: "Built area",
    8: "Bare ground",
    9: "Snow/ice",
    10: "Clouds",
    11: "Rangeland",
}

LC_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Impact Observatory 10m LULC, 9-class V1 (Sentinel-2 derived)",
)


@register("io_lulc_9class")
class IOLULC9ClassConnector(STACMixin, BaseConnector):
    slug = "io_lulc_9class"
    display_name = "Impact Observatory 10m LULC (9-class)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="IO 10m Annual LULC (9-class V1)",
                description="Global 10m LULC from Sentinel-2 (Impact Observatory / Esri), 9-class V1",
                variables=[LC_VAR],
                resolution_m=10,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="CC-BY-4.0",
                citation="Karra et al. 2021, Impact Observatory / Esri 10m LULC (9-class)",
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

        datetime_range = None
        if time_range:
            datetime_range = f"{time_range.start.isoformat()}/{time_range.end.isoformat()}"

        items = await self._stac_search(
            catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox,
            datetime_range=datetime_range,
        )
        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="land_cover", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0,
                pixel_count=0, provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {COLLECTION} (no items)",
            )

        # Prefer the most recent year so repeated overlapping items collapse
        # to a single deterministic choice.
        items = sorted(items, key=lambda it: it.get("id", ""), reverse=True)
        item = items[0]

        assets = item.get("assets", {})
        asset_key = "data" if "data" in assets else next(iter(assets), "")
        if not asset_key:
            raise DataFormatError(self.slug, "STAC item has no assets")

        cog_href = assets[asset_key].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")
        cog_href = self._sign_planetary_computer(cog_href)

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask,
            nodata=nodata if nodata is not None else 0,
            aggregation=AggregationMethod.DISTRIBUTION,
            data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {IO9_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', '?')}",
        )
