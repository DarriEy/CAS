# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""End-to-end extraction tests — one live test per registered provider.

These hit upstream providers, so they are marked ``network`` and excluded
from the default CI run.  Run the full sweep with:

    pytest tests/test_e2e_extract.py -m network -v

Or a single provider:

    pytest tests/test_e2e_extract.py -m network -k usgs_3dep -v

Each test runs a real ``extract()`` over a coverage-derived test polygon
and asserts the connector did not fail.  A connector that raises
(network, parse, auth-misconfig, ...) is a regression — DEGRADED (no data
over the test area) and auth-gated providers are tolerated.
"""

from __future__ import annotations

import math

import pytest

from cas.core.models import ProviderStatus
from cas.core.registry import discover, list_providers
from cas.monitor.geometry_check import LAND_ANCHORS, coverage_test_geometry
from cas.monitor.health import check_provider

discover()
ALL_PROVIDERS = sorted(list_providers())


@pytest.mark.network
@pytest.mark.parametrize("slug", ALL_PROVIDERS)
async def test_provider_extract(slug):
    """A registered provider must complete an extraction without erroring."""
    result = await check_provider(slug)

    if result.status == ProviderStatus.DOWN:
        pytest.fail(f"{slug} extraction failed: {result.error}")

    # UNKNOWN = auth-gated (no creds in CI); DEGRADED = no data over the test
    # polygon. Both are acceptable; only DOWN is a connector regression.
    assert result.status in (
        ProviderStatus.HEALTHY,
        ProviderStatus.DEGRADED,
        ProviderStatus.UNKNOWN,
    )

    if result.status == ProviderStatus.HEALTHY:
        assert result.test_value is None or math.isfinite(result.test_value)
        assert result.datasets_available > 0


# ── coverage_test_geometry unit tests (no network) ──────────────────


def _bbox(min_lon, min_lat, max_lon, max_lat):
    from cas.core.models import BoundingBox
    return BoundingBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


def _polygon_bounds(geom):
    ring = geom.coordinates[0]
    lons = [pt[0] for pt in ring]
    lats = [pt[1] for pt in ring]
    return min(lons), min(lats), max(lons), max(lats)


def test_global_bbox_picks_first_land_anchor():
    """A global provider should anchor on central US (the first anchor)."""
    geom = coverage_test_geometry(_bbox(-180, -90, 180, 90))
    min_lon, min_lat, max_lon, max_lat = _polygon_bounds(geom)
    cx, cy = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    assert (cx, cy) == pytest.approx(LAND_ANCHORS[0], abs=0.01)


def test_regional_bbox_stays_inside_coverage():
    """A Norway-only bbox must yield a polygon fully inside that bbox."""
    norway = _bbox(4.0, 58.0, 31.0, 71.0)
    geom = coverage_test_geometry(norway)
    min_lon, min_lat, max_lon, max_lat = _polygon_bounds(geom)
    assert norway.min_lon <= min_lon <= max_lon <= norway.max_lon
    assert norway.min_lat <= min_lat <= max_lat <= norway.max_lat


def test_narrow_bbox_with_no_anchor_uses_centroid():
    """A tiny bbox containing no anchor falls back to its centroid."""
    tiny = _bbox(100.0, 10.0, 100.2, 10.2)
    geom = coverage_test_geometry(tiny)
    min_lon, min_lat, max_lon, max_lat = _polygon_bounds(geom)
    assert tiny.min_lon <= min_lon <= max_lon <= tiny.max_lon
    assert tiny.min_lat <= min_lat <= max_lat <= tiny.max_lat
    cx = (min_lon + max_lon) / 2
    assert cx == pytest.approx((tiny.min_lon + tiny.max_lon) / 2, abs=0.05)


def test_polygon_is_never_degenerate():
    """Even a sub-degree bbox produces a polygon with positive area."""
    geom = coverage_test_geometry(_bbox(0.0, 0.0, 0.001, 0.001))
    min_lon, min_lat, max_lon, max_lat = _polygon_bounds(geom)
    assert max_lon > min_lon
    assert max_lat > min_lat
