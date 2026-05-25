# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""CORINE Land Cover connector — European 100m land cover.

Available via Copernicus Data Space STAC or direct download.
STAC access requires free Copernicus registration:
  https://dataspace.copernicus.eu/
"""

from __future__ import annotations

import os
import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.stac import STACMixin
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
from cas.extract.zonal import compute_zonal_stats, rasterize_geometry

logger = structlog.get_logger()

REGISTRATION_URL = "https://dataspace.copernicus.eu/"

CORINE_L1_CLASSES = {
    1: "Artificial surfaces",
    2: "Agricultural areas",
    3: "Forest and semi-natural areas",
    4: "Wetlands",
    5: "Water bodies",
}

CORINE_CLASSES = {
    111: "Continuous urban fabric",
    112: "Discontinuous urban fabric",
    121: "Industrial or commercial units",
    122: "Road and rail networks",
    123: "Port areas",
    124: "Airports",
    131: "Mineral extraction sites",
    132: "Dump sites",
    133: "Construction sites",
    141: "Green urban areas",
    142: "Sport and leisure facilities",
    211: "Non-irrigated arable land",
    212: "Permanently irrigated land",
    213: "Rice fields",
    221: "Vineyards",
    222: "Fruit trees and berry plantations",
    223: "Olive groves",
    231: "Pastures",
    241: "Annual crops associated with permanent crops",
    242: "Complex cultivation patterns",
    243: "Agriculture with natural vegetation",
    244: "Agro-forestry areas",
    311: "Broad-leaved forest",
    312: "Coniferous forest",
    313: "Mixed forest",
    321: "Natural grasslands",
    322: "Moors and heathland",
    323: "Sclerophyllous vegetation",
    324: "Transitional woodland-shrub",
    331: "Beaches, dunes, sands",
    332: "Bare rocks",
    333: "Sparsely vegetated areas",
    334: "Burnt areas",
    335: "Glaciers and perpetual snow",
    411: "Inland marshes",
    412: "Peat bogs",
    421: "Salt marshes",
    422: "Salines",
    423: "Intertidal flats",
    511: "Water courses",
    512: "Water bodies",
    521: "Coastal lagoons",
    522: "Estuaries",
    523: "Sea and ocean",
}

LAND_COVER_VAR = Variable(
    name="land_cover",
    units="class",
    data_type=DataType.CATEGORICAL,
    description="44-class CORINE land cover (3-level hierarchy)",
)


@register("corine_lc")
class CORINELandCoverConnector(STACMixin, BaseConnector):
    slug = "corine_lc"
    display_name = "CORINE Land Cover (Europe)"
    base_url = "https://stac.dataspace.copernicus.eu/v1"
    protocol = "stac_cog"

    def _check_access(self) -> None:
        token = self.config.get("token") or os.environ.get("CAS_COPERNICUS_TOKEN", "")
        if not token:
            raise RegistrationRequiredError(
                self.slug,
                REGISTRATION_URL,
                "Free Copernicus Data Space registration required.\n"
                "1. Register at: https://dataspace.copernicus.eu/\n"
                "2. Get an access token via the Copernicus identity API\n"
                "3. Set the token:\n"
                "   export CAS_COPERNICUS_TOKEN=your_token\n"
                "Alternative: download CORINE directly from\n"
                "  https://land.copernicus.eu/en/products/corine-land-cover",
            )

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name="CORINE Land Cover 100m (Europe)",
                description="European 100m land cover (1990, 2000, 2006, 2012, 2018). 44 classes.",
                variables=[LAND_COVER_VAR],
                resolution_m=100,
                crs="EPSG:3035",
                bbox=BoundingBox(min_lon=-32, min_lat=27, max_lon=45, max_lat=72),
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="Copernicus (free)",
                citation="Copernicus Land Monitoring Service, CORINE Land Cover",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        self._check_access()

        start_time = time.monotonic()
        bbox = self._geometry_to_bbox(geometry)

        datetime_range = "2018-01-01/2018-12-31"
        if time_range:
            datetime_range = f"{time_range.start.isoformat()}/{time_range.end.isoformat()}"

        items = await self._stac_search(
            catalog_url=self.base_url,
            collections=["corine-land-cover-raster"],
            bbox=bbox,
            datetime_range=datetime_range,
        )

        if not items:
            return AttributeResult(
                dataset_id=dataset_id, variable="land_cover", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="STAC search: corine-land-cover-raster (no items)",
            )

        item = self._select_best_item(items)
        asset_keys = list(item.get("assets", {}).keys())
        if not asset_keys:
            raise DataFormatError(self.slug, "STAC item has no assets")

        cog_href = item["assets"][asset_keys[0]].get("href", "")
        if not cog_href:
            raise DataFormatError(self.slug, "STAC asset has no href")

        token = os.environ.get("CAS_COPERNICUS_TOKEN", "")
        if token:
            cog_href = f"{cog_href}?token={token}"

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
            value = {CORINE_CLASSES.get(int(k), f"class_{k}"): v for k, v in value.items()}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable="land_cover", value=value,
            units="class_fraction", aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"STAC COG: corine-land-cover/{item.get('id', 'unknown')}",
        )
