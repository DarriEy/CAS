# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for coverage-aware health test geometry, incl. explicit anchors."""

from cas.core.models import BoundingBox
from cas.monitor.test_geometry import coverage_test_geometry


def _bbox(a, b, c, d):
    return BoundingBox(min_lon=a, min_lat=b, max_lon=c, max_lat=d)


def _center(geom):
    pts = geom.coordinates[0][:4]
    return sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4


def test_anchor_inside_bbox_is_used():
    # A land anchor inside the bbox should be chosen over the centroid.
    geom = coverage_test_geometry(_bbox(5.0, 44.0, 12.0, 49.0), 30.0)
    cx, _ = _center(geom)
    assert 5.0 < cx < 12.0


def test_explicit_anchor_overrides_land_anchor():
    # An explicit anchor must win, e.g. a coastal mangrove point inside a
    # continental Africa bbox whose default land anchor would be inland.
    anchor = (39.35, -7.90)  # Rufiji Delta
    geom = coverage_test_geometry(_bbox(-26.0, -35.0, 58.0, 38.0), 25.0, anchor=anchor)
    cx, cy = _center(geom)
    assert abs(cx - anchor[0]) < 0.01
    assert abs(cy - anchor[1]) < 0.01
