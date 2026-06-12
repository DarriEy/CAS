# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Regional-unit selection for unit-structured mirror datasets (design §3).

Each unit-structured dataset gets a **bbox → unit ids** resolver so lazy
materialization touches only the units a query actually needs. Resolvers are
registered per slug; static extent tables live here for datasets with
published regional extents (RGI first-order regions), and index-driven
resolution (TDX vpu-boundaries) plugs into the same registry.

The static extents are deliberately **generous** selection boxes: selecting
one extra region costs a small download; missing one silently drops features
— a correctness failure. They are used only to choose units, never to clip.
"""

from __future__ import annotations

from collections.abc import Callable

from cas.core.config import Settings
from cas.core.exceptions import MirrorUnitError

Bbox = tuple[float, float, float, float]
"""(min_lon, min_lat, max_lon, max_lat), EPSG:4326."""

UnitResolver = Callable[[Bbox, Settings], list[str]]

_UNIT_RESOLVERS: dict[str, UnitResolver] = {}


def register_unit_resolver(slug: str, resolver: UnitResolver) -> None:
    _UNIT_RESOLVERS[slug] = resolver


def units_for_bbox(slug: str, bbox: Bbox, settings: Settings) -> list[str]:
    """Resolve the unit ids of ``slug`` intersecting an EPSG:4326 bbox."""
    resolver = _UNIT_RESOLVERS.get(slug)
    if resolver is None:
        raise MirrorUnitError(
            f"Mirror dataset '{slug}' has no unit resolver registered; "
            f"pass explicit unit ids instead."
        )
    return resolver(bbox, settings)


def _bbox_intersects(a: Bbox, b: Bbox) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _static_resolver(table: dict[str, Bbox]) -> UnitResolver:
    def resolve(bbox: Bbox, settings: Settings) -> list[str]:
        _ = settings
        return [unit for unit, extent in table.items() if _bbox_intersects(bbox, extent)]

    return resolve


# ── RGI 7.0 first-order regions ─────────────────────────────────────
#
# 19 first-order regions (live-verified against the NSIDC regional_files
# listing, 2026-06-12 — note RGI 7.0 has 19 regions; the 20-region table in
# older tooling includes a region 20 that does not exist upstream).
# Extents are generous selection boxes derived from the published region
# outlines; region 01 (Alaska) glaciers on the far Aleutians west of the
# antimeridian (~172°E) are additionally caught by an antimeridian guard.

RGI_REGION_NAMES: dict[str, str] = {
    "01": "alaska",
    "02": "western_canada_usa",
    "03": "arctic_canada_north",
    "04": "arctic_canada_south",
    "05": "greenland_periphery",
    "06": "iceland",
    "07": "svalbard_jan_mayen",
    "08": "scandinavia",
    "09": "russian_arctic",
    "10": "north_asia",
    "11": "central_europe",
    "12": "caucasus_middle_east",
    "13": "central_asia",
    "14": "south_asia_west",
    "15": "south_asia_east",
    "16": "low_latitudes",
    "17": "southern_andes",
    "18": "new_zealand",
    "19": "subantarctic_antarctic_islands",
}

RGI_REGION_BBOXES: dict[str, tuple[Bbox, ...]] = {
    # Alaska, plus the far-Aleutian lobe east of the antimeridian (Attu).
    "01": ((-180.0, 50.0, -120.0, 73.0), (170.0, 50.0, 180.0, 56.0)),
    "02": ((-133.0, 36.0, -100.0, 61.0),),
    "03": ((-130.0, 73.0, -55.0, 85.0),),
    "04": ((-100.0, 56.0, -55.0, 76.0),),
    # Greenland periphery as two lobes (south of 70°N the east coast stays
    # west of 38°W) so an Iceland-only domain doesn't select Greenland.
    "05": ((-75.0, 58.0, -38.0, 70.0), (-75.0, 67.5, -7.0, 85.0)),
    "06": ((-26.0, 63.0, -12.0, 67.5),),
    "07": ((-12.0, 70.0, 36.0, 82.0),),
    "08": ((3.0, 57.0, 33.0, 72.0),),
    "09": ((28.0, 67.0, 180.0, 83.0),),
    "10": ((60.0, 40.0, 180.0, 80.0),),
    "11": ((-6.0, 40.0, 20.0, 49.0),),
    "12": ((30.0, 30.0, 56.0, 46.0),),
    "13": ((60.0, 25.0, 105.0, 50.0),),
    "14": ((60.0, 23.0, 85.0, 41.0),),
    "15": ((75.0, 20.0, 106.0, 41.0),),
    "16": ((-100.0, -26.0, 145.0, 25.0),),
    "17": ((-79.0, -57.0, -60.0, -16.0),),
    "18": ((165.0, -48.0, 180.0, -38.0),),
    "19": ((-180.0, -90.0, 180.0, -44.0),),
}


def rgi_regions_for_bbox(bbox: Bbox) -> list[str]:
    """RGI 7.0 region ids whose extent intersects an EPSG:4326 bbox."""
    return sorted(
        unit
        for unit, extents in RGI_REGION_BBOXES.items()
        if any(_bbox_intersects(bbox, extent) for extent in extents)
    )


register_unit_resolver("rgi7", lambda bbox, settings: rgi_regions_for_bbox(bbox))
