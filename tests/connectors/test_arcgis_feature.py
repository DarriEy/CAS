"""Tests for the ArcGIS REST FeatureServer connector (hydrobasins / hydrolakes).

Offline: the ArcGIS /query GeoJSON is mocked, so these exercise the GeoJSON parser, the
continuous area-weighted-mean / value-at-point aggregation, the NA-vs-SA layer routing, and the
MapServer pagination-param omission — without network access.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cas.connectors.arcgis_feature import (
    HydroBasinsConnector,
    HydroLakesConnector,
    HydroRiversConnector,
    _parse_geojson_features,
)
from cas.core.models import Geometry, QualityFlag

BASINS_BASE = "https://imapinvasives.natureserve.org/arcgis/rest/services/hydrobasins/MapServer"
LAKES_BASE = "https://services8.arcgis.com/GyR85gR88mMqIY4t/arcgis/rest/services/HydroLAKES_v10/FeatureServer"
RIVERS_BASE = "https://services5.arcgis.com/Lw3jWlmYzUzOr2jO/arcgis/rest/services/HydroSHEDS/FeatureServer"


def _square(lon0: float, lat0: float, lon1: float, lat1: float) -> list:
    """A GeoJSON polygon ring (lon,lat) for an axis-aligned box."""
    return [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]]


def _fc(*features: tuple[list, dict]) -> bytes:
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": ring}, "properties": props}
                for ring, props in features
            ],
        }
    ).encode()


def _line_fc(*features: tuple[list, dict]) -> bytes:
    """A GeoJSON FeatureCollection of LineStrings: ((coords), props)."""
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}
                for coords, props in features
            ],
        }
    ).encode()


# Two adjacent unit squares carrying different UP_AREA values.
TWO_BASINS = _fc(
    (_square(0.0, 0.0, 1.0, 1.0), {"UP_AREA": 100.0}),
    (_square(1.0, 0.0, 2.0, 1.0), {"UP_AREA": 300.0}),
)


class TestGeoJSONParser:
    def test_parses_numeric_attribute(self):
        feats = _parse_geojson_features(json.loads(TWO_BASINS), "UP_AREA")
        assert len(feats) == 2
        assert {v for _, v in feats} == {100.0, 300.0}

    def test_skips_null_and_nonnumeric(self):
        ring = _square(0, 0, 1, 1)

        def feat(up_area):
            geom = {"type": "Polygon", "coordinates": ring}
            return {"type": "Feature", "geometry": geom, "properties": {"UP_AREA": up_area}}

        payload = {"type": "FeatureCollection", "features": [feat(None), feat("n/a"), feat(5.0)]}
        feats = _parse_geojson_features(payload, "UP_AREA")
        assert [v for _, v in feats] == [5.0]


class TestHydroBasinsConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with HydroBasinsConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].provider == "hydrobasins"
        assert datasets[0].protocol.value == "rest"
        assert datasets[0].variables[0].name == "upstream_area"

    @pytest.mark.asyncio
    @respx.mock
    async def test_polygon_area_weighted_mean(self):
        # Query lon[0.5,1.5] x lat[0,1]: half over UP_AREA=100, half over 300 -> mean 200.
        respx.get(f"{BASINS_BASE}/2/query").mock(return_value=httpx.Response(200, content=TWO_BASINS))
        query = Geometry(type="Polygon", coordinates=_square(0.5, 0.0, 1.5, 1.0))  # SA latitude
        async with HydroBasinsConnector() as conn:
            r = await conn.extract("hydrobasins:upstream_area", query)
        assert r.aggregation.value == "mean"
        assert r.value == pytest.approx(200.0)
        assert r.coverage_fraction == pytest.approx(1.0)
        assert r.quality == QualityFlag.GOOD
        assert r.pixel_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_point_returns_covering_basin(self):
        respx.get(f"{BASINS_BASE}/2/query").mock(return_value=httpx.Response(200, content=TWO_BASINS))
        point = Geometry(type="Point", coordinates=[1.5, 0.5])  # inside the UP_AREA=300 square
        async with HydroBasinsConnector() as conn:
            r = await conn.extract("hydrobasins:upstream_area", point)
        assert r.value == pytest.approx(300.0)
        assert r.coverage_fraction == pytest.approx(1.0)

    @pytest.mark.asyncio
    @respx.mock
    async def test_layer_routing_north_vs_south(self):
        # North America (lat>=7) -> layer 1; South America (lat<7) -> layer 2.
        na = _fc((_square(-100, 39, -99, 40), {"UP_AREA": 42.0}))
        sa = _fc((_square(-63, -5, -62, -4), {"UP_AREA": 99.0}))
        north = respx.get(f"{BASINS_BASE}/1/query").mock(return_value=httpx.Response(200, content=na))
        south = respx.get(f"{BASINS_BASE}/2/query").mock(return_value=httpx.Response(200, content=sa))
        async with HydroBasinsConnector() as conn:
            await conn.extract("hydrobasins:upstream_area", Geometry(type="Point", coordinates=[-99.5, 39.5]))
            await conn.extract("hydrobasins:upstream_area", Geometry(type="Point", coordinates=[-62.5, -4.5]))
        assert north.called and south.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_pagination_param_omitted_for_mapserver(self):
        route = respx.get(f"{BASINS_BASE}/2/query").mock(return_value=httpx.Response(200, content=TWO_BASINS))
        async with HydroBasinsConnector() as conn:
            await conn.extract("hydrobasins:upstream_area", Geometry(type="Point", coordinates=[1.5, 0.5]))
        assert "resultRecordCount" not in route.calls.last.request.url.params

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_is_missing(self):
        respx.get(f"{BASINS_BASE}/2/query").mock(return_value=httpx.Response(200, content=_fc()))
        query = Geometry(type="Polygon", coordinates=_square(0.5, 0.0, 1.5, 1.0))
        async with HydroBasinsConnector() as conn:
            r = await conn.extract("hydrobasins:upstream_area", query)
        assert r.value is None
        assert r.quality == QualityFlag.MISSING
        assert r.coverage_fraction == 0.0

    @pytest.mark.asyncio
    @respx.mock
    async def test_arcgis_error_envelope_raises(self):
        from cas.core.exceptions import DataFormatError

        respx.get(f"{BASINS_BASE}/2/query").mock(
            return_value=httpx.Response(200, json={"error": {"code": 400, "message": "Pagination is not supported."}})
        )
        async with HydroBasinsConnector() as conn:
            with pytest.raises(DataFormatError):
                await conn.extract("hydrobasins:upstream_area", Geometry(type="Point", coordinates=[1.5, 0.5]))


class TestHydroLakesConnector:
    @pytest.mark.asyncio
    @respx.mock
    async def test_pagination_param_present_for_featureserver(self):
        route = respx.get(f"{LAKES_BASE}/0/query").mock(
            return_value=httpx.Response(200, content=_fc((_square(50, 40, 51, 42), {"Depth_avg": 200.5})))
        )
        point = Geometry(type="Point", coordinates=[50.5, 41.0])
        async with HydroLakesConnector() as conn:
            r = await conn.extract("hydrolakes:lake_depth", point)
        assert r.value == pytest.approx(200.5)
        assert "resultRecordCount" in route.calls.last.request.url.params

    @pytest.mark.asyncio
    @respx.mock
    async def test_point_on_land_is_missing(self):
        respx.get(f"{LAKES_BASE}/0/query").mock(return_value=httpx.Response(200, content=_fc()))
        point = Geometry(type="Point", coordinates=[60.0, 45.0])  # no lake polygon here
        async with HydroLakesConnector() as conn:
            r = await conn.extract("hydrolakes:lake_depth", point)
        assert r.value is None
        assert r.quality == QualityFlag.MISSING


class TestHydroRiversConnector:
    # A big river (discharge 1000) and a small tributary (discharge 5), both crossing the point.
    TWO_RIVERS = _line_fc(
        ([[-60.1, -3.0], [-59.9, -3.0]], {"DIS_AV_CMS": 1000.0}),
        ([[-60.0, -3.1], [-60.0, -2.9]], {"DIS_AV_CMS": 5.0}),
    )

    @pytest.mark.asyncio
    async def test_list_datasets_is_line_mode(self):
        async with HydroRiversConnector() as conn:
            datasets = await conn.list_datasets()
        assert datasets[0].provider == "hydrorivers"
        assert datasets[0].variables[0].name == "river_discharge"

    @pytest.mark.asyncio
    @respx.mock
    async def test_point_returns_dominant_river_discharge(self):
        respx.get(f"{RIVERS_BASE}/17/query").mock(return_value=httpx.Response(200, content=self.TWO_RIVERS))
        point = Geometry(type="Point", coordinates=[-60.0, -3.0])  # both rivers within 5km buffer
        async with HydroRiversConnector() as conn:
            r = await conn.extract("hydrorivers:river_discharge", point)
        assert r.aggregation.value == "max"  # dominant (largest) river
        assert r.value == pytest.approx(1000.0)
        assert r.pixel_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_envelope_widened_by_search_buffer(self):
        route = respx.get(f"{RIVERS_BASE}/17/query").mock(return_value=httpx.Response(200, content=self.TWO_RIVERS))
        async with HydroRiversConnector() as conn:
            await conn.extract("hydrorivers:river_discharge", Geometry(type="Point", coordinates=[-60.0, -3.0]))
        # geometry_to_bbox gives a ~100m point box; line mode must widen it by ~0.05° so nearby
        # rivers are actually fetched. Parse the xmin from the geometry envelope param.
        geom_param = route.calls.last.request.url.params["geometry"]
        xmin = float(geom_param.split(",")[0])
        assert xmin < -60.0 - 0.04  # widened well beyond the bare point envelope

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_river_in_range_is_missing(self):
        respx.get(f"{RIVERS_BASE}/17/query").mock(return_value=httpx.Response(200, content=_line_fc()))
        point = Geometry(type="Point", coordinates=[-55.0, -10.0])  # headwaters: no level-6+ river
        async with HydroRiversConnector() as conn:
            r = await conn.extract("hydrorivers:river_discharge", point)
        assert r.value is None
        assert r.quality == QualityFlag.MISSING
        assert r.coverage_fraction == 0.0
