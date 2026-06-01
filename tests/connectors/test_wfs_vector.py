"""Tests for the WFS vector-feature connector (usgs_geology / SGMC lithology).

Offline: the BRGM/USGS GML is mocked, so these exercise the GML parser (axis order,
interior holes) and the area-weighted attribute overlay without network access.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from cas.connectors.wfs_vector import USGSGeologyConnector, _parse_features
from cas.core.models import Geometry, QualityFlag

WFS_URL = "https://mrdata.usgs.gov/services/wfs/sgmc"


def _mock_wfs(content: bytes) -> None:
    respx.get(WFS_URL).mock(
        return_value=httpx.Response(200, content=content, headers={"content-type": "text/xml"})
    )


def _gml_square(lith: str, lat0: float, lon0: float, lat1: float, lon1: float) -> str:
    """One GML 3.1.1 Lithology feature: an axis-aligned square (posList is lat,lon)."""
    pos = f"{lat0} {lon0} {lat0} {lon1} {lat1} {lon1} {lat1} {lon0} {lat0} {lon0}"
    return f"""
    <gml:featureMember>
      <ms:Lithology>
        <ms:msGeometry>
          <gml:MultiSurface srsName="EPSG:4326">
            <gml:surfaceMember>
              <gml:Polygon>
                <gml:exterior>
                  <gml:LinearRing><gml:posList>{pos}</gml:posList></gml:LinearRing>
                </gml:exterior>
              </gml:Polygon>
            </gml:surfaceMember>
          </gml:MultiSurface>
        </ms:msGeometry>
        <ms:lith62>{lith}</ms:lith62>
      </ms:Lithology>
    </gml:featureMember>"""


def _gml_doc(*features: str) -> bytes:
    body = "".join(features)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" '
        'xmlns:gml="http://www.opengis.net/gml" xmlns:ms="http://mapserver.gis.umn.edu/mapserver">'
        f"{body}</wfs:FeatureCollection>"
    ).encode()


# Two adjacent unit-squares: Granite over lon[0,1], Sandstone over lon[1,2], lat[0,1].
TWO_UNITS = _gml_doc(
    _gml_square("Granite", 0.0, 0.0, 1.0, 1.0),
    _gml_square("Sandstone", 0.0, 1.0, 1.0, 2.0),
)


class TestGMLParser:
    def test_axis_order_and_attribute(self):
        feats = _parse_features(_gml_doc(_gml_square("Granite", 0.0, 0.0, 1.0, 2.0)), "lith62")
        assert len(feats) == 1
        geom, val = feats[0]
        assert val == "Granite"
        # posList was lat,lon; parser must return lon,lat -> bounds (lon0,lat0,lon1,lat1).
        assert geom.bounds == pytest.approx((0.0, 0.0, 2.0, 1.0))

    def test_interior_ring_becomes_hole(self):
        outer = "0 0 0 4 4 4 4 0 0 0"  # lat,lon
        inner = "1 1 1 2 2 2 2 1 1 1"
        gml = (
            '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" '
            'xmlns:gml="http://www.opengis.net/gml" xmlns:ms="http://mapserver.gis.umn.edu/mapserver">'
            "<gml:featureMember><ms:Lithology><ms:msGeometry><gml:Polygon>"
            f"<gml:exterior><gml:LinearRing><gml:posList>{outer}</gml:posList></gml:LinearRing></gml:exterior>"
            f"<gml:interior><gml:LinearRing><gml:posList>{inner}</gml:posList></gml:LinearRing></gml:interior>"
            "</gml:Polygon></ms:msGeometry><ms:lith62>Limestone</ms:lith62></ms:Lithology></gml:featureMember>"
            "</wfs:FeatureCollection>"
        ).encode()
        feats = _parse_features(gml, "lith62")
        geom, val = feats[0]
        assert val == "Limestone"
        # 4x4 outer minus 1x1 hole = 15.
        assert geom.area == pytest.approx(15.0)


class TestUSGSGeologyConnector:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        async with USGSGeologyConnector() as conn:
            datasets = await conn.list_datasets()
        assert len(datasets) == 1
        assert datasets[0].provider == "usgs_geology"
        assert datasets[0].protocol.value == "wfs"

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_area_weighted_distribution(self):
        _mock_wfs(TWO_UNITS)
        # Query lon[0.5,1.5] x lat[0,1]: half in Granite, half in Sandstone.
        query = Geometry(type="Polygon", coordinates=[[
            [0.5, 0.0], [1.5, 0.0], [1.5, 1.0], [0.5, 1.0], [0.5, 0.0],
        ]])
        async with USGSGeologyConnector() as conn:
            r = await conn.extract("usgs_geology:lithology", query)
        assert r.aggregation.value == "distribution"
        assert r.value == {"Granite": pytest.approx(0.5), "Sandstone": pytest.approx(0.5)}
        assert r.coverage_fraction == pytest.approx(1.0)
        assert r.quality == QualityFlag.GOOD
        assert r.pixel_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_point_returns_containing_unit(self):
        _mock_wfs(TWO_UNITS)
        point = Geometry(type="Point", coordinates=[0.5, 0.5])  # inside the Granite square
        async with USGSGeologyConnector() as conn:
            r = await conn.extract("usgs_geology:lithology", point)
        assert r.value == {"Granite": pytest.approx(1.0)}
        assert r.coverage_fraction == pytest.approx(1.0)

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_no_features_is_missing(self):
        _mock_wfs(_gml_doc())
        query = Geometry(type="Polygon", coordinates=[[
            [0.5, 0.0], [1.5, 0.0], [1.5, 1.0], [0.5, 1.0], [0.5, 0.0],
        ]])
        async with USGSGeologyConnector() as conn:
            r = await conn.extract("usgs_geology:lithology", query)
        assert r.value is None
        assert r.quality == QualityFlag.MISSING
        assert r.coverage_fraction == 0.0
