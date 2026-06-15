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


# ── HydroBASINS continental regions (geofabric, slice 2b) ───────────
#
# Units are ``{region}_lev{level}`` (e.g. ``na_lev06``). The static table
# selects the continental region(s); the Pfafstetter level is the consumer's
# choice and is carried in the unit id (see :func:`hydrobasins_units_for_bbox`).
# Extents mirror the native SYMFLUENCE handler's region table.

HYDROBASINS_REGION_BBOXES: dict[str, Bbox] = {
    "af": (-20.0, -35.0, 55.0, 38.0),   # Africa
    "ar": (-180.0, 51.0, 180.0, 84.0),  # Arctic (Greenland, Iceland, ...)
    "as": (57.0, 1.0, 180.0, 56.0),     # Central-South Asia
    "au": (95.0, -56.0, 180.0, 0.0),    # Australia-Oceania
    "eu": (-25.0, 12.0, 70.0, 72.0),    # Europe-Middle East
    "na": (-138.0, 5.0, -52.0, 63.0),   # North America
    "sa": (-93.0, -56.0, -32.0, 15.0),  # South America
    "si": (57.0, 45.0, 180.0, 84.0),    # Siberia
}


def hydrobasins_regions_for_bbox(bbox: Bbox) -> list[str]:
    """HydroBASINS continental region codes intersecting an EPSG:4326 bbox."""
    return sorted(
        r for r, ext in HYDROBASINS_REGION_BBOXES.items() if _bbox_intersects(bbox, ext)
    )


def hydrobasins_units_for_bbox(bbox: Bbox, level: int) -> list[str]:
    """Full ``{region}_lev{level}`` unit ids for a bbox at a Pfafstetter level."""
    if not 1 <= level <= 12:
        raise MirrorUnitError(f"HydroBASINS Pfafstetter level must be 1-12, got {level}.")
    return [f"{r}_lev{level:02d}" for r in hydrobasins_regions_for_bbox(bbox)]


# ── MERIT-Basins Pfafstetter Level-1 regions (geofabric, slice 2b) ──
#
# Nine continental Pfaf-L1 regions (codes 1-9). The mapping follows the
# OFFICIAL MERIT-Basins ReadMe (reachhydro Drive folder, read 2026-06-12):
# "1: Africa, 2: Europe, 3: North Asia, 4: South Asia, 5: Oceania and South
# Asian Islands, 6: South America, 7: North America, 8: Arctic Region,
# 9: Greenland" — and region 9's extent was verified empirically against the
# distributed shapefiles (lon -73..-12, lat 60..84 — Greenland+Iceland).
# NOTE: the native SYMFLUENCE handler ships a DIFFERENT table (1=Amazon,
# 9=Australia, ...) that contradicts the official mapping; it is wrong, not
# this one. Boxes are generous selection boxes (overlap is safe; a missed
# region silently drops features).

MERIT_PFAF_BBOXES: dict[str, Bbox] = {
    "1": (-20.0, -36.0, 56.0, 38.0),    # Africa
    "2": (-25.0, 12.0, 70.0, 72.0),     # Europe (incl. Middle East fringe)
    "3": (55.0, 40.0, 180.0, 84.0),     # North Asia / Siberia
    "4": (55.0, 0.0, 150.0, 56.0),      # South Asia (Middle East to E Asia)
    "5": (90.0, -56.0, 180.0, 21.0),    # Oceania + South Asian islands
    "6": (-93.0, -56.0, -32.0, 15.0),   # South America
    "7": (-170.0, 5.0, -52.0, 62.0),    # North America
    "8": (-180.0, 50.0, -50.0, 84.0),   # Arctic Region (N. American Arctic)
    "9": (-75.0, 58.0, -10.0, 84.0),    # Greenland (verified: -73..-12, 60..84)
}


def merit_pfaf_for_bbox(bbox: Bbox) -> list[str]:
    """MERIT-Basins Pfaf-L1 region codes intersecting an EPSG:4326 bbox."""
    return sorted(
        (p for p, ext in MERIT_PFAF_BBOXES.items() if _bbox_intersects(bbox, ext)),
        key=int,
    )


register_unit_resolver("merit_basins", lambda bbox, settings: merit_pfaf_for_bbox(bbox))


# ── TDX-Hydro / GEOGLOWS v2 VPUs (geofabric, slice 2b) ──────────────
#
# ~125 VPUs that cannot be enumerated statically — they come from the
# upstream ``vpu-boundaries.gpkg`` index (Hive-partitioned S3). The index is
# cached under the mirror root; the resolver spatial-joins the query bbox to
# the VPU boundaries (logic ported from the native tdx_hydro handler).

_GEOGLOWS_S3_BASE = "https://geoglows-v2.s3.us-west-2.amazonaws.com"
TDX_VPU_BOUNDARIES_URL = f"{_GEOGLOWS_S3_BASE}/hydrography-global/vpu-boundaries.gpkg"


def _tdx_index_path(settings: Settings):
    return settings.mirror_dir / "tdx_hydro" / "_index" / "vpu-boundaries.gpkg"


def ensure_tdx_index(settings: Settings):
    """Download (once) and return the cached VPU-boundaries GeoDataFrame."""
    import httpx

    from cas.mirror.convert import _import_geopandas

    gpd = _import_geopandas()
    index_path = _tdx_index_path(settings)
    if not index_path.is_file():
        index_path.parent.mkdir(parents=True, exist_ok=True)
        part = index_path.with_suffix(".part")
        with httpx.stream("GET", TDX_VPU_BOUNDARIES_URL, follow_redirects=True,
                          timeout=httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)) as resp:
            resp.raise_for_status()
            with open(part, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
        part.replace(index_path)
    return gpd.read_file(index_path)


def tdx_vpus_for_bbox(bbox: Bbox, settings: Settings) -> list[str]:
    """TDX-Hydro VPU codes intersecting an EPSG:4326 bbox (via the index)."""
    from shapely.geometry import box as shapely_box

    index = ensure_tdx_index(settings)
    if index.crs is not None and str(index.crs).upper() not in ("EPSG:4326", "OGC:CRS84"):
        index = index.to_crs("EPSG:4326")
    query = shapely_box(*bbox)
    hits = index[index.geometry.intersects(query)]
    col = "VPUCode" if "VPUCode" in hits.columns else hits.columns[0]
    return sorted(str(v) for v in hits[col].unique())


register_unit_resolver("tdx_hydro", tdx_vpus_for_bbox)
