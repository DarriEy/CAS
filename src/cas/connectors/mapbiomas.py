# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MapBiomas Brazil annual land use / land cover (30 m) — windowed COG read.

MapBiomas is the reference wall-to-wall LULC product for Brazil (1985–present,
Landsat-based, 30 m). Each annual Collection-9 mosaic is published as a single
public Cloud-Optimized GeoTIFF on Google Cloud Storage, so we read only the
window overlapping the requested geometry via rasterio /vsicurl (the same
windowed pattern as the ETH canopy-height connector) rather than the whole
~3 GB raster.

This is the headline South America connector: global products (ESA WorldCover,
Dynamic World, IO LULC) cover Brazil at a coarse legend, while MapBiomas carries
a far richer, agriculture-aware Brazilian legend (soybean, sugar cane, coffee,
pasture, mosaic-of-uses, ...).
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

# Latest published Collection-9 annual coverage.
_COLLECTION = 9
_YEAR = 2023
_COG_URL = (
    "https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/"
    f"collection_{_COLLECTION}/lclu/coverage/brasil_coverage_{_YEAR}.tif"
)
# MapBiomas uses pixel value 0 for "no data" (outside coverage / unmapped).
_NODATA = 0

# MapBiomas Collection 9 legend (pixel code -> class name).
MAPBIOMAS_CLASSES = {
    1: "forest",
    3: "forest_formation",
    4: "savanna_formation",
    5: "mangrove",
    6: "floodable_forest",
    49: "wooded_sandbank_vegetation",
    10: "herbaceous_shrubby_vegetation",
    11: "wetland",
    12: "grassland",
    32: "hypersaline_tidal_flat",
    29: "rocky_outcrop",
    50: "herbaceous_sandbank_vegetation",
    13: "other_non_forest_formations",
    14: "farming",
    15: "pasture",
    18: "agriculture",
    19: "temporary_crop",
    39: "soybean",
    20: "sugar_cane",
    40: "rice",
    62: "cotton",
    41: "other_temporary_crops",
    36: "perennial_crop",
    46: "coffee",
    47: "citrus",
    35: "palm_oil",
    48: "other_perennial_crops",
    9: "forest_plantation",
    21: "mosaic_of_uses",
    22: "non_vegetated_area",
    23: "beach_dune_sand",
    24: "urban_area",
    30: "mining",
    25: "other_non_vegetated_areas",
    26: "water",
    33: "river_lake_ocean",
    31: "aquaculture",
    27: "not_observed",
}

LC_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description=f"MapBiomas Collection {_COLLECTION} land use/land cover ({_YEAR})",
)


@register("mapbiomas_brazil")
class MapBiomasBrazilConnector(STACMixin, BaseConnector):
    slug = "mapbiomas_brazil"
    display_name = "MapBiomas Brazil LULC 30m"
    base_url = "https://storage.googleapis.com/mapbiomas-public"
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name=self.display_name,
                description=(f"MapBiomas Brazil annual LULC, Collection {_COLLECTION}, {_YEAR} (30 m)"),
                variables=[LC_VAR],
                resolution_m=30.0,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-74.0, min_lat=-34.0, max_lon=-28.8, max_lat=5.3),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA 4.0",
                citation="MapBiomas Project, Collection 9 (Brazil)",
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

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=_COG_URL,
                bbox=bbox,
                geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data,
            mask=mask,
            nodata=nodata if nodata is not None else _NODATA,
            aggregation=AggregationMethod.DISTRIBUTION,
            data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {MAPBIOMAS_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id,
            variable="land_cover",
            value=value,
            units="class",
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=pixel_count,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"MapBiomas COG: collection_{_COLLECTION}/{_YEAR}",
        )
