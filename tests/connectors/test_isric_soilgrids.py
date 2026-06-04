"""Tests for ISRIC SoilGrids connector."""

from __future__ import annotations

import httpx
import pytest
import respx

from cas.connectors.isric_soilgrids import (
    _MAX_QUERY_POINTS,
    GRID_SPACING_DEG,
    ISRICSoilGridsConnector,
    _generate_grid_points,
)
from cas.core.models import Geometry, QualityFlag

MOCK_RESPONSE_JSON = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-96.55, 39.05]},
    "properties": {
        "layers": [
            {
                "name": "clay",
                "depths": [
                    {
                        "range": {"top_depth": 0, "bottom_depth": 5},
                        "label": "0-5cm",
                        "values": {"mean": 382},
                    }
                ],
            }
        ]
    },
}


@pytest.fixture
def test_geometry() -> Geometry:
    return Geometry(
        type="Polygon",
        coordinates=[[
            [-96.6, 39.0],
            [-96.5, 39.0],
            [-96.5, 39.1],
            [-96.6, 39.1],
            [-96.6, 39.0],
        ]],
    )


class TestISRICSoilGridsConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with ISRICSoilGridsConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 78  # 13 variables * 6 depths
        assert all(ds.provider == "isric_soilgrids" for ds in datasets)
        assert any("clay" in ds.id for ds in datasets)

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_clay(self, test_geometry):
        respx.get("https://rest.isric.org/soilgrids/v2.0/properties/query").mock(
            return_value=httpx.Response(200, json=MOCK_RESPONSE_JSON)
        )

        async with ISRICSoilGridsConnector() as conn:
            result = await conn.extract(
                dataset_id="isric_soilgrids:clay_0-5cm",
                geometry=test_geometry,
            )

        assert result.variable == "clay"
        assert result.units == "g/kg"
        assert result.value is not None
        assert result.value == pytest.approx(382.0)
        assert result.quality in (QualityFlag.GOOD, QualityFlag.PARTIAL)
        assert result.pixel_count > 0

    @pytest.mark.asyncio
    async def test_extract_unknown_variable(self, test_geometry):
        from cas.core.exceptions import DataFormatError

        async with ISRICSoilGridsConnector() as conn:
            with pytest.raises(DataFormatError, match="Unknown variable"):
                await conn.extract(
                    dataset_id="isric_soilgrids:unknown_var_0-5cm",
                    geometry=test_geometry,
                )

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_no_data(self, test_geometry):
        respx.get("https://rest.isric.org/soilgrids/v2.0/properties/query").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )

        async with ISRICSoilGridsConnector() as conn:
            result = await conn.extract(
                dataset_id="isric_soilgrids:clay_0-5cm",
                geometry=test_geometry,
            )

        assert result.quality == QualityFlag.MISSING
        assert result.value is None

    def test_dataset_metadata(self):
        import asyncio

        async def _check():
            async with ISRICSoilGridsConnector() as conn:
                datasets = await conn.list_datasets()
                clay_ds = [d for d in datasets if "clay_0-5cm" in d.id][0]
                assert clay_ds.resolution_m == 250
                assert clay_ds.license == "CC-BY-4.0"
                assert clay_ds.variables[0].units == "g/kg"

        asyncio.run(_check())


class TestGridPointCap:
    def test_grid_never_exceeds_cap_even_for_huge_bbox(self):
        # A 10°×10° bbox at ~250m spacing would be millions of points; the
        # rate-limited ISRIC point API must never be hit with more than the cap.
        pts = _generate_grid_points((-100.0, 30.0, -90.0, 40.0), GRID_SPACING_DEG)
        assert 0 < len(pts) <= _MAX_QUERY_POINTS

    def test_tiny_bbox_yields_at_least_one_point(self):
        pts = _generate_grid_points((10.0, 50.0, 10.0001, 50.0001), GRID_SPACING_DEG)
        assert len(pts) >= 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_fires_at_most_cap_requests(self, test_geometry):
        route = respx.get(
            "https://rest.isric.org/soilgrids/v2.0/properties/query"
        ).mock(return_value=httpx.Response(200, json=MOCK_RESPONSE_JSON))

        async with ISRICSoilGridsConnector() as conn:
            await conn.extract(
                dataset_id="isric_soilgrids:clay_0-5cm", geometry=test_geometry
            )

        # The 0.1°×0.1° test polygon would be ~1000 points at native spacing;
        # the cap must hold it to <= _MAX_QUERY_POINTS REST calls.
        assert route.call_count <= _MAX_QUERY_POINTS

    @pytest.mark.asyncio
    @respx.mock
    async def test_one_hanging_point_does_not_sink_extract(self, test_geometry):
        # Half the points error/hang; extract should still return a value from
        # the points that came back (one bad point can't stall or fail the run).
        calls = {"n": 0}

        def _flaky(request):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise httpx.ConnectTimeout("simulated hang")
            return httpx.Response(200, json=MOCK_RESPONSE_JSON)

        respx.get("https://rest.isric.org/soilgrids/v2.0/properties/query").mock(
            side_effect=_flaky
        )

        async with ISRICSoilGridsConnector() as conn:
            result = await conn.extract(
                dataset_id="isric_soilgrids:clay_0-5cm", geometry=test_geometry
            )

        assert result.value == pytest.approx(382.0)
        assert 0.0 < result.coverage_fraction < 1.0
