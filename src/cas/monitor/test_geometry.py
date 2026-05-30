# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Coverage-aware test geometries for end-to-end extraction checks.

A passthrough service has 200+ providers spanning every continent.  A
single fixed test polygon (e.g. central Kansas) only exercises providers
whose coverage includes that point — every country-specific connector
(Norway, Australia, Argentina, ...) returns empty data there and looks
"down" even when it works perfectly.

This module derives a *valid* test polygon from each provider's declared
coverage ``bbox``: it picks the first curated land anchor that falls
inside the provider's coverage and builds a small polygon around it,
falling back to the bbox centroid when no anchor matches (narrow or
exotic coverage).  The result is a polygon that is both inside coverage
and (almost always) over land, so a healthy connector returns real data.
"""

from __future__ import annotations

from cas.core.models import BoundingBox, Geometry

# Curated land anchors (lon, lat), ordered by how broadly useful they are.
# central_us is first so global providers — which match every anchor — get a
# reliable, data-rich land point.  The rest cover regions with deep
# country-specific provider coverage so regional connectors find data inside
# their own bbox.
LAND_ANCHORS: list[tuple[float, float]] = [
    (-96.55, 39.05),   # central US (Kansas)
    (8.23, 46.80),     # Switzerland / Alps
    (10.74, 59.91),    # Norway (Oslo)
    (-1.50, 52.50),    # United Kingdom (England)
    (-8.00, 53.30),    # Ireland
    (5.30, 52.10),     # Netherlands
    (25.00, 62.00),    # Finland
    (10.00, 51.00),    # Germany
    (12.50, 42.00),    # Italy
    (-3.70, 40.40),    # Spain (Madrid)
    (145.00, -37.00),  # Australia (Victoria)
    (138.00, 36.00),   # Japan
    (172.50, -43.50),  # New Zealand
    (-19.00, 64.50),   # Iceland
    (-64.00, -34.00),  # Argentina
    (-47.00, -15.50),  # Brazil
    (37.00, -1.00),    # Kenya (East Africa)
    (28.00, -26.00),   # South Africa
    (77.50, 28.50),    # India
    (-114.00, 51.00),  # Canada (Alberta)
    (110.00, 30.00),   # China
]

# Half-width bounds for a test polygon, in degrees (~1 km / ~5.5 km at the
# equator). The polygon is scaled up for coarse-resolution providers so it
# always spans several pixels — a fixed ~1 km square rounds to zero pixels
# for, e.g., a 5 km biomass product ("width and height must be > 0").
_MIN_HALF_SIZE = 0.005
_MAX_HALF_SIZE = 0.05
_DEG_PER_M = 1.0 / 111_320.0  # rough degrees-per-metre at the equator
_MIN_PIXELS_ACROSS = 6        # ensure the test window covers >= ~6 pixels


def _point_in_bbox(lon: float, lat: float, bbox: BoundingBox) -> bool:
    return bbox.min_lon <= lon <= bbox.max_lon and bbox.min_lat <= lat <= bbox.max_lat


def _square(center_lon: float, center_lat: float, half: float) -> Geometry:
    lon, lat = center_lon, center_lat
    return Geometry(
        type="Polygon",
        coordinates=[[
            [lon - half, lat - half],
            [lon + half, lat - half],
            [lon + half, lat + half],
            [lon - half, lat + half],
            [lon - half, lat - half],
        ]],
    )


def coverage_test_geometry(bbox: BoundingBox, resolution_m: float | None = None) -> Geometry:
    """Return a small test polygon guaranteed to lie inside ``bbox``.

    Prefers a curated land anchor within coverage; otherwise uses the
    bbox centroid.  The polygon is sized to span at least a few pixels of
    the provider's ``resolution_m`` (so coarse products don't produce a
    zero-pixel request) and clamped so it never spills outside ``bbox``.
    """
    width = bbox.max_lon - bbox.min_lon
    height = bbox.max_lat - bbox.min_lat

    # Start from the baseline ~1 km half-width, then grow it to cover at
    # least a few pixels for coarse-resolution providers.
    half = _MIN_HALF_SIZE
    if resolution_m:
        half = max(half, (_MIN_PIXELS_ACROSS / 2.0) * resolution_m * _DEG_PER_M)
    half = min(half, _MAX_HALF_SIZE)

    # Never let the polygon exceed the coverage extent; never degenerate.
    half = min(half, width * 0.4, height * 0.4)
    half = max(half, 1e-4)

    # Default to the bbox centroid; override with the first land anchor inside.
    center_lon = (bbox.min_lon + bbox.max_lon) / 2.0
    center_lat = (bbox.min_lat + bbox.max_lat) / 2.0
    for anchor_lon, anchor_lat in LAND_ANCHORS:
        if _point_in_bbox(anchor_lon, anchor_lat, bbox):
            center_lon, center_lat = anchor_lon, anchor_lat
            break

    # Clamp the center so the square stays strictly within the bbox.
    center_lon = min(max(center_lon, bbox.min_lon + half), bbox.max_lon - half)
    center_lat = min(max(center_lat, bbox.min_lat + half), bbox.max_lat - half)

    return _square(center_lon, center_lat, half)
