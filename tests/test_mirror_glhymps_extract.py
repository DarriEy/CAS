# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""GLHYMPS stats-over-mirror tests (slice 2c).

The resurrected GLHYMPS connector is mirror-backed: ``extract()`` reads the
local GeoParquet mirror and returns area-weighted permeability/porosity through
the normal engine. Tests use a synthetic GLHYMPS-shaped mirror (two
half-coverage polygons in an equal-area CRS) so the area weighting and
coverage_fraction are exactly checkable, and assert the manifest-integrity
health path (no network).
"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

import cas
from cas.core.models import Geometry, ProviderStatus
from cas.core.registry import discover, get_connector
from cas.mirror import register_mirror_dataset, unregister_mirror_dataset
from cas.mirror.models import MirrorDataset, MirrorLicense, MirrorSource

GLHYMPS_CRS = "ESRI:54034"  # World Cylindrical Equal Area (GLHYMPS source CRS)
GLHYMPS_URL = "https://mirror.invalid/glhymps_test.zip"


def _glhymps_zip(tmp_path) -> bytes:
    """Two side-by-side equal-area squares with known permeability/porosity.

    In EPSG:4326 the polygons tile lon [0,2)×[0,1) and [2,4)×[0,1); a query
    polygon spanning lon [1,3]×[0,1] covers half of each, so the area-weighted
    mean is the simple average of the two cells.
    """
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {
            "logK_Ice": [-12.0, -16.0],
            "logK_Ferr": [-11.0, -15.0],
            "Porosity": [0.10, 0.30],
            "geometry": [box(0.0, 0.0, 2.0, 1.0), box(2.0, 0.0, 4.0, 1.0)],
        },
        crs="EPSG:4326",
    ).to_crs(GLHYMPS_CRS)

    build = tmp_path / "_glhymps_build"
    build.mkdir(exist_ok=True)
    gdf.to_file(build / "glhymps_v2.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(build.iterdir()):
            zf.write(f, arcname=f"GLHYMPS/{f.name}")
    return buf.getvalue()


@pytest.fixture
def glhymps_mirror(tmp_path):
    """Register a GLHYMPS test mirror (slug glhymps==2.0) served via respx."""
    ds = MirrorDataset(
        slug="glhymps",
        version="2.0",
        display_name="GLHYMPS test",
        sources=[MirrorSource(url=GLHYMPS_URL, archive_name="glhymps.zip", size_bytes_approx=1000)],
        shapefile_patterns=["*glhymps*.shp", "*.shp"],
        source_crs_note="World Cylindrical Equal Area",
        default_buffer_deg=0.1,
        known_columns=["logK_Ice", "logK_Ferr", "Porosity"],
        license=MirrorLicense(
            license="CC-BY-4.0", license_verified=True,
            attribution="GLHYMPS test", license_flags=[],
        ),
        citation="Huscroft et al. 2018 (test).",
    )
    discover()  # ensure the glhymps connector is registered
    # Replace the shipped glhymps==2.0 registration for the test.
    unregister_mirror_dataset("glhymps", "2.0")
    register_mirror_dataset(ds)
    yield ds
    unregister_mirror_dataset("glhymps", "2.0")
    # Re-import datasets module state by re-registering the shipped entry.
    import importlib

    import cas.mirror.datasets as dsmod

    importlib.reload(dsmod)


def _polygon(min_lon, min_lat, max_lon, max_lat) -> Geometry:
    return Geometry(
        type="Polygon",
        coordinates=[[
            [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
            [min_lon, max_lat], [min_lon, min_lat],
        ]],
    )


class TestGLHYMPSConnectorRegistration:
    def test_registered_as_mirror_kind(self):
        discover()
        cls = get_connector("glhymps")
        assert cls.slug == "glhymps"
        assert cls.kind == "mirror"
        assert cls.protocol == "mirror"

    @pytest.mark.asyncio
    async def test_lists_three_variables(self, mirror_root, glhymps_mirror):
        cls = get_connector("glhymps")
        async with cls() as conn:
            datasets = await conn.list_datasets()
        ids = {d.id for d in datasets}
        assert ids == {
            "glhymps:permeability",
            "glhymps:permeability_permafrost_free",
            "glhymps:porosity",
        }
        for d in datasets:
            assert d.provider == "glhymps"
            assert d.variables[0].units in ("log10(m2)", "fraction")


class TestGLHYMPSExtract:
    @respx.mock
    @pytest.mark.asyncio
    async def test_area_weighted_mean_over_two_cells(self, mirror_root, glhymps_mirror, tmp_path):
        respx.get(GLHYMPS_URL).mock(
            return_value=httpx.Response(200, content=_glhymps_zip(tmp_path))
        )
        cls = get_connector("glhymps")
        # Query spans half of each cell → mean of the two cell values.
        query = _polygon(1.0, 0.0, 3.0, 1.0)
        async with cls() as conn:
            perm = await conn.extract("glhymps:permeability", query)
            poro = await conn.extract("glhymps:porosity", query)

        assert perm.value == pytest.approx(-14.0, abs=0.05)   # mean(-12, -16)
        assert poro.value == pytest.approx(0.20, abs=0.005)   # mean(0.10, 0.30)
        assert perm.coverage_fraction == pytest.approx(1.0, abs=0.02)
        assert perm.units == "log10(m2)"
        assert perm.aggregation.value == "mean"
        assert "curated mirror glhymps==2.0" in perm.provenance

    @respx.mock
    @pytest.mark.asyncio
    async def test_single_cell_query(self, mirror_root, glhymps_mirror, tmp_path):
        respx.get(GLHYMPS_URL).mock(
            return_value=httpx.Response(200, content=_glhymps_zip(tmp_path))
        )
        cls = get_connector("glhymps")
        query = _polygon(0.2, 0.2, 1.8, 0.8)  # entirely inside cell 1
        async with cls() as conn:
            perm = await conn.extract("glhymps:permeability", query)
            ferr = await conn.extract("glhymps:permeability_permafrost_free", query)
        assert perm.value == pytest.approx(-12.0)
        assert ferr.value == pytest.approx(-11.0)
        assert perm.coverage_fraction == pytest.approx(1.0, abs=0.02)

    @respx.mock
    @pytest.mark.asyncio
    async def test_point_query_returns_covering_polygon(self, mirror_root, glhymps_mirror, tmp_path):
        respx.get(GLHYMPS_URL).mock(
            return_value=httpx.Response(200, content=_glhymps_zip(tmp_path))
        )
        cls = get_connector("glhymps")
        point = Geometry(type="Point", coordinates=[3.0, 0.5])  # in cell 2
        async with cls() as conn:
            poro = await conn.extract("glhymps:porosity", point)
        assert poro.value == pytest.approx(0.30)
        assert poro.coverage_fraction == 1.0

    @respx.mock
    @pytest.mark.asyncio
    async def test_query_off_coverage_is_missing(self, mirror_root, glhymps_mirror, tmp_path):
        respx.get(GLHYMPS_URL).mock(
            return_value=httpx.Response(200, content=_glhymps_zip(tmp_path))
        )
        cls = get_connector("glhymps")
        query = _polygon(50.0, 50.0, 51.0, 51.0)  # nowhere near the cells
        async with cls() as conn:
            result = await conn.extract("glhymps:porosity", query)
        assert result.value is None
        assert result.coverage_fraction == 0.0
        assert result.quality.value == "missing"

    @respx.mock
    @pytest.mark.asyncio
    async def test_through_engine(self, mirror_root, glhymps_mirror, tmp_path):
        """GLHYMPS serves stats through the normal engine like any provider."""
        respx.get(GLHYMPS_URL).mock(
            return_value=httpx.Response(200, content=_glhymps_zip(tmp_path))
        )
        request = cas.AttributeRequest(
            geometry=_polygon(1.0, 0.0, 3.0, 1.0),
            dataset_ids=["glhymps:permeability", "glhymps:porosity"],
        )
        response = await cas.extract(request)
        values = {r.dataset_id: r.value for r in response.results}
        assert values["glhymps:permeability"] == pytest.approx(-14.0, abs=0.05)
        assert values["glhymps:porosity"] == pytest.approx(0.20, abs=0.005)


class TestGLHYMPSHealth:
    @pytest.mark.asyncio
    async def test_health_degraded_when_not_synced(self, mirror_root, glhymps_mirror):
        from cas.monitor.health import check_provider

        result = await check_provider("glhymps")
        # Not materialized: DEGRADED, not an outage — and no network was hit.
        assert result.status == ProviderStatus.DEGRADED
        assert "not synced" in (result.error or "")
        assert result.datasets_available == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_healthy_when_materialized(self, mirror_root, glhymps_mirror, tmp_path):
        from cas.mirror import ensure_materialized
        from cas.monitor.health import check_provider

        respx.get(GLHYMPS_URL).mock(
            return_value=httpx.Response(200, content=_glhymps_zip(tmp_path))
        )
        ensure_materialized("glhymps==2.0")
        result = await check_provider("glhymps")
        assert result.status == ProviderStatus.HEALTHY

    def test_reachability_skips_mirror(self):
        from cas.monitor.reachability import _probe_url

        assert _probe_url("(local mirror)", "mirror") == ("(local mirror)", "SKIP")
