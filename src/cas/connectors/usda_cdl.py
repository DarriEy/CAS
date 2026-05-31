# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""USDA Cropland Data Layer (CDL) via Planetary Computer STAC+COG.

The legacy CropScape REST API (nassgeodata.gmu.edu) was abandoned: its
polygon-clipping endpoints hang server-side. CDL is published as annual
30 m COGs in the Planetary Computer ``usda-cdl`` collection, which we read
directly with a server-side window — the same pattern as the other PC
land-cover connectors.
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

logger = structlog.get_logger()

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "usda-cdl"
# The usda-cdl collection splits products into separate items per tile/year
# (cropland_*, cultivated_*, frequency_*); we want the cropland classification.
CROPLAND_ASSET = "cropland"
# Latest cropland year currently published in the PC collection (2008–2021).
DEFAULT_YEAR = 2021

CDL_MAJOR_CLASSES = {
    1: "Corn", 2: "Cotton", 3: "Rice", 4: "Sorghum", 5: "Soybeans",
    6: "Sunflower", 10: "Peanuts", 11: "Tobacco", 12: "Sweet corn",
    21: "Barley", 22: "Durum wheat", 23: "Spring wheat", 24: "Winter wheat",
    25: "Other small grains", 26: "Dbl crop winter wheat/soybeans",
    27: "Rye", 28: "Oats", 29: "Millet",
    36: "Alfalfa", 37: "Other hay/non-alfalfa", 41: "Sugarbeets",
    42: "Dry beans", 43: "Potatoes", 44: "Other crops",
    61: "Fallow/idle cropland", 63: "Forest", 64: "Shrubland",
    65: "Barren", 81: "Clouds/no data", 82: "Developed",
    83: "Water", 87: "Wetlands", 88: "Nonag/undefined",
    111: "Open water", 121: "Developed/open space",
    122: "Developed/low intensity", 123: "Developed/med intensity",
    124: "Developed/high intensity", 131: "Barren",
    141: "Deciduous forest", 142: "Evergreen forest", 143: "Mixed forest",
    152: "Shrubland", 176: "Grassland/pasture",
    190: "Woody wetlands", 195: "Herbaceous wetlands",
}

CROP_VAR = Variable(
    name="cropland",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="Crop-specific land cover (~130 classes)",
)


@register("usda_cdl")
class USDAcroplandConnector(STACMixin, BaseConnector):
    slug = "usda_cdl"
    display_name = "USDA CDL (Cropland)"
    base_url = STAC_URL
    protocol = "stac_cog"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:cropland",
                provider=self.slug,
                name="USDA Cropland Data Layer 30m",
                description="US 30m crop-specific land cover (annual, 2008–2021)",
                variables=[CROP_VAR],
                resolution_m=30,
                crs="EPSG:5070",
                bbox=BoundingBox(min_lon=-126, min_lat=23, max_lon=-66, max_lat=50),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="Public Domain",
                citation="USDA NASS, Cropland Data Layer (via Planetary Computer)",
            )
        ]

    def _cropland_items(self, items: list[dict]) -> list[dict]:
        return [it for it in items if CROPLAND_ASSET in it.get("assets", {})]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)
        year = time_range.start.year if time_range else DEFAULT_YEAR

        # Prefer the requested year; fall back to whatever cropland items exist.
        items = self._cropland_items(await self._stac_search(
            catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox,
            datetime_range=f"{year}-01-01/{year}-12-31",
        ))
        if not items:
            all_items = self._cropland_items(await self._stac_search(
                catalog_url=STAC_URL, collections=[COLLECTION], bbox=bbox,
            ))
            # Latest available year (item id is "cropland_<year>_<tile>").
            items = sorted(all_items, key=lambda it: it.get("id", ""), reverse=True)

        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="cropland", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0,
                pixel_count=0, provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"STAC: {COLLECTION} (no cropland items)",
            )

        item = items[0]
        cog_href = item["assets"][CROPLAND_ASSET].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC cropland asset has no href")
        cog_href = self._sign_planetary_computer(cog_href)

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_href, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"COG read failed: {e}") from e

        from cas.extract.zonal import compute_zonal_stats, rasterize_geometry

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        # CDL uses 0 for background/no-data outside CONUS.
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask,
            nodata=nodata if nodata is not None else 0,
            aggregation=AggregationMethod.DISTRIBUTION, data_type=DataType.CATEGORICAL,
        )

        if isinstance(value, dict):
            value = {CDL_MAJOR_CLASSES.get(int(k), f"crop_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="cropland", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: {COLLECTION}/{item.get('id', '?')}",
        )
