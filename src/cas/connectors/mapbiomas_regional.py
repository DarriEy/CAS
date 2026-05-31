# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""MapBiomas regional / trinational annual LULC (30 m) — windowed COG reads.

Beyond Brazil (see ``mapbiomas.py``), the MapBiomas network publishes
wall-to-wall, Landsat-based land-use/land-cover for several South American
regions as public Cloud-Optimized GeoTIFFs on the same Google Cloud Storage
bucket. Each annual mosaic is one COG, so we read only the window overlapping
the requested geometry via rasterio ``/vsicurl`` (same pattern as the Brazil
connector and the ETH canopy-height connector) rather than the whole raster.

Regions wired here:

* **Amazonia** (RAISG / Pan-Amazonia) — the trinational Amazon basin.
* **Chaco** — the Gran Chaco dry forest (Argentina/Paraguay/Bolivia/Brazil).
* **Pampa** — the trinational Pampa grasslands (Brazil/Uruguay/Argentina).
* Per-country initiatives: **Bolivia, Colombia, Peru, Paraguay, Uruguay,
  Venezuela** (South America) and **Indonesia** (the Asia coverage leg).

These fill the South American (and South-East Asian) coverage gap left by the
global products (ESA WorldCover, Dynamic World, IO LULC), which carry only a
coarse legend, while MapBiomas uses a far richer, ecosystem-specific legend
(dry-forest, several grassland classes, ...).

The COG paths below were each confirmed by opening the raster over ``/vsicurl``
(uint8, EPSG:4326, single band, nodata unset → ``_NODATA`` fallback). Note the
sub-path differs per region: Amazonia and Pampa publish under ``coverage/``
while Chaco (like Brazil) uses ``lclu/coverage/`` — do not assume one template.
"""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.mapbiomas import MAPBIOMAS_CLASSES
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

logger = structlog.get_logger(__name__)

# MapBiomas COGs are single-band uint8 with no embedded nodata; 0 = "not observed".
_NODATA = 0

# IMPORTANT: each MapBiomas initiative ships its OWN legend. The integer codes
# are NOT interchangeable across initiatives — e.g. code 43 is "Citrus" in the
# Brazil-integrated legend but "Closed Grassland" in Chaco, and Chaco also
# redefines 6, 11 and 36. So every regional connector carries its own
# code->name dict; unknown codes fall back to a generic ``class_<n>`` label
# rather than being mislabelled.
#
# Chaco Collection-5 legend, verified against the official MapBiomas Chaco
# "Legend Code" PDF (chaco.mapbiomas.org/en/legend-codes/).
CHACO_CLASSES = {
    1: "natural_wooded_vegetation",
    3: "closed_woodland",
    4: "open_woodland",
    45: "sparse_woodland",
    6: "flooded_woodland",
    10: "natural_non_wooded_vegetation",
    12: "grassland",
    43: "closed_grassland",
    42: "open_grassland",
    44: "sparse_grassland",
    11: "flooded_grassland",
    14: "agricultural_and_livestock",
    15: "pasture",
    18: "agriculture",
    19: "annual_crops",
    57: "single_crop",
    58: "multiple_crop",
    36: "shrub_plantation",
    9: "forest_plantation",
    22: "non_vegetated_area",
    23: "beach_dune_sand",
    24: "urban_area",
    25: "other_non_vegetated_areas",
    61: "salt_flat",
    26: "water",
    27: "not_observed",
}

# Amazonia (RAISG) follows the MapBiomas integrated legend, so it reuses the
# Brazil code->name dict. Pampa also reuses it as a base; its grassland/crop
# subclasses (42-45, 57, 58) are not in the integrated dict and therefore
# surface as ``class_<n>`` until its authoritative legend is wired in (the
# trinational Pampa "Legend Code" PDF). These fall-throughs are labelling-only
# and never affect the numeric distribution.


class _MapBiomasRegionalConnector(STACMixin, BaseConnector):
    """Shared windowed-COG extraction for a MapBiomas regional LULC mosaic.

    Subclasses set the region-specific class attributes below and the
    ``@register`` slug.
    """

    base_url = "https://storage.googleapis.com/mapbiomas-public"
    protocol = "stac_cog"

    # --- region-specific configuration (set by subclasses) ---
    region_label: str = ""
    collection: int = 0
    year: int = 0
    cog_url: str = ""
    extent: BoundingBox = BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)
    citation: str = ""
    # code->class-name legend for THIS initiative (default: integrated legend).
    legend: dict[int, str] = MAPBIOMAS_CLASSES
    # Curated on-land, in-coverage anchor for the health sweep. The default
    # coverage geometry picks a generic land anchor that can fall just outside
    # a regional data mask (e.g. the RAISG Amazonia boundary) and read as
    # nodata; check_provider reads this attribute to override the anchor.
    health_anchor: tuple[float, float] | None = None

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:land_cover",
                provider=self.slug,
                name=self.display_name,
                description=(
                    f"MapBiomas {self.region_label} annual LULC, "
                    f"Collection {self.collection}, {self.year} (30 m)"
                ),
                variables=[
                    Variable(
                        name="land_cover",
                        units="class",
                        data_type=DataType.CATEGORICAL,
                        description=(
                            f"MapBiomas {self.region_label} Collection {self.collection} "
                            f"land use/land cover ({self.year})"
                        ),
                    )
                ],
                resolution_m=30.0,
                crs="EPSG:4326",
                bbox=self.extent,
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.STAC_COG,
                license="CC-BY-SA 4.0",
                citation=self.citation,
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
                cog_url=self.cog_url,
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
            value = {self.legend.get(int(k), f"class_{k}"): v for k, v in value.items()}

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
            provenance=f"MapBiomas COG: {self.region_label}/collection_{self.collection}/{self.year}",
        )


@register("mapbiomas_amazonia")
class MapBiomasAmazoniaConnector(_MapBiomasRegionalConnector):
    """MapBiomas Amazonia (RAISG / Pan-Amazonia) annual LULC, 30 m."""

    slug = "mapbiomas_amazonia"
    display_name = "MapBiomas Amazonia LULC 30m"
    region_label = "Amazonia (RAISG)"
    collection = 5
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/amazonia/"
        f"collection_{collection}/coverage/amazonia_coverage_{year}.tif"
    )
    # Pan-Amazonia basin extent (approx, EPSG:4326).
    extent = BoundingBox(min_lon=-79.6, min_lat=-20.0, max_lon=-44.0, max_lat=10.0)
    citation = "MapBiomas Amazonia Project (RAISG), Collection 5"
    # Central Amazon várzea (deep inside the RAISG mask); the generic anchor
    # (-60,-10) lands just outside the data mask and reads as nodata.
    health_anchor = (-62.2, -4.0)


@register("mapbiomas_chaco")
class MapBiomasChacoConnector(_MapBiomasRegionalConnector):
    """MapBiomas Chaco (Gran Chaco dry forest) annual LULC, 30 m."""

    slug = "mapbiomas_chaco"
    display_name = "MapBiomas Chaco LULC 30m"
    region_label = "Gran Chaco"
    collection = 4
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/chaco/"
        f"collection_{collection}/lclu/coverage/chaco_coverage_{year}.tif"
    )
    # Gran Chaco extent (Argentina/Paraguay/Bolivia/Brazil), approx EPSG:4326.
    extent = BoundingBox(min_lon=-66.0, min_lat=-33.0, max_lon=-57.0, max_lat=-16.0)
    citation = "MapBiomas Chaco Project, Collection 4"
    legend = CHACO_CLASSES
    # Argentine/Paraguayan Chaco interior (verified on-data).
    health_anchor = (-60.5, -22.5)


@register("mapbiomas_pampa")
class MapBiomasPampaConnector(_MapBiomasRegionalConnector):
    """MapBiomas Pampa (trinational Pampa grasslands) annual LULC, 30 m."""

    slug = "mapbiomas_pampa"
    display_name = "MapBiomas Pampa LULC 30m"
    region_label = "Pampa (trinational)"
    collection = 3
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/pampa/"
        f"collection_{collection}/coverage/pampa_coverage_{year}.tif"
    )
    # Trinational Pampa extent (Brazil/Uruguay/Argentina), approx EPSG:4326.
    extent = BoundingBox(min_lon=-64.0, min_lat=-39.0, max_lon=-49.0, max_lat=-28.0)
    citation = "MapBiomas Pampa Project, Collection 3"
    # Uruguay / Rio Grande do Sul Pampa grassland (verified on-data).
    health_anchor = (-55.0, -31.5)


# ── Per-country MapBiomas initiatives ───────────────────────────────────────
#
# MapBiomas also publishes single-country annual LULC for several South American
# countries and Indonesia, on the same bucket and pixel convention. Their
# sampled class codes all fall within the integrated MAPBIOMAS_CLASSES legend
# (unseen codes degrade to ``class_<n>`` labels, never affecting the numeric
# distribution), so they reuse the default legend. Each COG path/year below was
# confirmed by opening the raster over ``/vsicurl``; the sub-path again varies
# per initiative (``coverage/`` vs ``lclu/coverage/``).


@register("mapbiomas_bolivia")
class MapBiomasBoliviaConnector(_MapBiomasRegionalConnector):
    """MapBiomas Bolivia annual LULC, 30 m."""

    slug = "mapbiomas_bolivia"
    display_name = "MapBiomas Bolivia LULC 30m"
    region_label = "Bolivia"
    collection = 1
    year = 2021
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/bolivia/"
        f"collection_{collection}/lclu/coverage/bolivia_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=-69.7, min_lat=-23.0, max_lon=-57.4, max_lat=-9.6)
    citation = "MapBiomas Bolivia Project, Collection 1"
    health_anchor = (-63.5, -16.5)  # Santa Cruz lowland forest/savanna


@register("mapbiomas_colombia")
class MapBiomasColombiaConnector(_MapBiomasRegionalConnector):
    """MapBiomas Colombia annual LULC, 30 m."""

    slug = "mapbiomas_colombia"
    display_name = "MapBiomas Colombia LULC 30m"
    region_label = "Colombia"
    collection = 1
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/colombia/"
        f"collection_{collection}/coverage/colombia_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=-79.1, min_lat=-4.3, max_lon=-66.8, max_lat=12.6)
    citation = "MapBiomas Colombia Project, Collection 1"
    health_anchor = (-73.5, 4.5)  # Llanos / Andean foothills


@register("mapbiomas_peru")
class MapBiomasPeruConnector(_MapBiomasRegionalConnector):
    """MapBiomas Peru annual LULC, 30 m."""

    slug = "mapbiomas_peru"
    display_name = "MapBiomas Peru LULC 30m"
    region_label = "Peru"
    collection = 1
    year = 2021
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/peru/"
        f"collection_{collection}/lclu/coverage/peru_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=-81.4, min_lat=-18.4, max_lon=-68.7, max_lat=-0.0)
    citation = "MapBiomas Peru Project, Collection 1"
    health_anchor = (-74.5, -10.0)  # Amazonian Peru (Ucayali)


@register("mapbiomas_paraguay")
class MapBiomasParaguayConnector(_MapBiomasRegionalConnector):
    """MapBiomas Paraguay annual LULC, 30 m."""

    slug = "mapbiomas_paraguay"
    display_name = "MapBiomas Paraguay LULC 30m"
    region_label = "Paraguay"
    collection = 1
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/paraguay/"
        f"collection_{collection}/coverage/paraguay_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=-62.7, min_lat=-27.6, max_lon=-54.2, max_lat=-19.3)
    citation = "MapBiomas Paraguay Project, Collection 1"
    health_anchor = (-57.5, -24.5)  # eastern Paraguay


@register("mapbiomas_uruguay")
class MapBiomasUruguayConnector(_MapBiomasRegionalConnector):
    """MapBiomas Uruguay annual LULC, 30 m."""

    slug = "mapbiomas_uruguay"
    display_name = "MapBiomas Uruguay LULC 30m"
    region_label = "Uruguay"
    collection = 1
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/uruguay/"
        f"collection_{collection}/coverage/uruguay_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=-58.5, min_lat=-35.0, max_lon=-53.0, max_lat=-30.0)
    citation = "MapBiomas Uruguay Project, Collection 1"
    health_anchor = (-56.0, -33.0)  # central Uruguay


@register("mapbiomas_venezuela")
class MapBiomasVenezuelaConnector(_MapBiomasRegionalConnector):
    """MapBiomas Venezuela annual LULC, 30 m."""

    slug = "mapbiomas_venezuela"
    display_name = "MapBiomas Venezuela LULC 30m"
    region_label = "Venezuela"
    collection = 1
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/venezuela/"
        f"collection_{collection}/coverage/venezuela_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=-73.4, min_lat=0.6, max_lon=-59.8, max_lat=12.2)
    citation = "MapBiomas Venezuela Project, Collection 1"
    health_anchor = (-66.0, 7.5)  # Llanos del Orinoco


@register("mapbiomas_indonesia")
class MapBiomasIndonesiaConnector(_MapBiomasRegionalConnector):
    """MapBiomas Indonesia annual LULC, 30 m (Asia coverage leg)."""

    slug = "mapbiomas_indonesia"
    display_name = "MapBiomas Indonesia LULC 30m"
    region_label = "Indonesia"
    collection = 2
    year = 2022
    cog_url = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/indonesia/"
        f"collection_{collection}/coverage/indonesia_coverage_{year}.tif"
    )
    extent = BoundingBox(min_lon=95.0, min_lat=-11.0, max_lon=141.0, max_lat=6.0)
    citation = "MapBiomas Indonesia Project, Collection 2"
    # The archipelago centroid is ocean; sample central Sumatra (Riau) instead.
    health_anchor = (101.5, 0.5)
