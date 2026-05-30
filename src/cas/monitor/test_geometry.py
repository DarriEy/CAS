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

# Maximum half-width of a test polygon, in degrees (~1 km at the equator).
_MAX_HALF_SIZE = 0.005


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


def coverage_test_geometry(bbox: BoundingBox) -> Geometry:
    """Return a small test polygon guaranteed to lie inside ``bbox``.

    Prefers a curated land anchor within coverage; otherwise uses the
    bbox centroid.  The polygon is sized to the coverage extent and
    clamped so it never spills outside ``bbox``.
    """
    width = bbox.max_lon - bbox.min_lon
    height = bbox.max_lat - bbox.min_lat

    # Keep the polygon comfortably inside coverage even for narrow bboxes.
    half = min(_MAX_HALF_SIZE, width * 0.1, height * 0.1)
    half = max(half, 1e-4)  # never degenerate to a point

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
