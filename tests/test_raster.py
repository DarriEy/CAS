"""Tests for the in-process raster mode (bbox-mode GeoTIFF extraction).

Covers: request-model validation, the STAC all-intersecting-items windowed
mosaic (synthetic 2-tile fixtures), the WCS native-resolution passthrough,
supports_raster capability gating, the engine's cache/QC bypass, and the
facade round-trip writing a real GeoTIFF.

All offline: STAC/WCS HTTP is mocked with respx; COG hrefs point at local
GeoTIFF tiles so rasterio never touches the network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import numpy as np
import pytest
import rasterio
import respx
from pydantic import ValidationError
from rasterio.transform import from_bounds

import cas
from cas.connectors.base import BaseConnector
from cas.connectors.protocols.stac import STACMixin
from cas.connectors.protocols.wcs import WCSMixin
from cas.core.exceptions import (
    DataFormatError,
    ExtractionError,
    ProtocolError,
    RasterUnsupportedError,
)
from cas.core.models import (
    AttributeRequest,
    BoundingBox,
    Dataset,
    DataType,
    OutputMode,
    Protocol,
    RasterResampling,
    RasterResult,
    TemporalExtent,
    TemporalType,
    Variable,
)
from cas.extract.engine import extract, extract_raster

NODATA = -9999.0
RES = 0.05  # degrees per pixel in the synthetic tiles
BBOX = (10.5, 50.2, 11.5, 50.8)  # straddles the tile seam at lon=11.0


# ── Synthetic tile fixtures ─────────────────────────────────────────


def _write_tile(
    path: Path,
    bounds: tuple[float, float, float, float],
    value: float,
    nodata_cols: tuple[int, ...] = (),
) -> None:
    width = round((bounds[2] - bounds[0]) / RES)
    height = round((bounds[3] - bounds[1]) / RES)
    arr = np.full((height, width), value, dtype=np.float32)
    for col in nodata_cols:
        arr[:, col] = NODATA
    transform = from_bounds(*bounds, width, height)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(arr, 1)


@pytest.fixture
def two_tiles(tmp_path: Path) -> tuple[Path, Path]:
    """Two adjacent 1°x1° tiles meeting at lon=11.0.

    West tile = 100.0 with its seam-adjacent column nodata; east tile = 200.0.
    A bbox straddling lon=11.0 must mosaic into ONE raster: 100s on the left,
    200s on the right, and the nodata stripe preserved at the seam.
    """
    west = tmp_path / "tile_west.tif"
    east = tmp_path / "tile_east.tif"
    _write_tile(west, (10.0, 50.0, 11.0, 51.0), 100.0, nodata_cols=(19,))
    _write_tile(east, (11.0, 50.0, 12.0, 51.0), 200.0)
    return west, east


def _stac_items(*tile_paths: Path) -> dict:
    return {
        "features": [
            {
                "id": f"tile_{p.stem}",
                "type": "Feature",
                "assets": {"data": {"href": str(p)}},
            }
            for p in tile_paths
        ]
    }


# ── Test connectors ─────────────────────────────────────────────────


TEST_DATASET = Dataset(
    id="test_stac:elevation",
    provider="test_stac",
    name="Test STAC DEM",
    variables=[Variable(name="elevation", units="m", data_type=DataType.CONTINUOUS)],
    resolution_m=30,
    crs="EPSG:4326",
    bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
    protocol=Protocol.STAC_COG,
    license="Test License",
)


class TwoTileSTACConnector(STACMixin, BaseConnector):
    slug = "test_stac"
    display_name = "Test STAC"
    base_url = "https://stac.test/api"
    protocol = "stac_cog"

    supports_raster = True
    stac_raster_collections = ("test-dem",)

    async def list_datasets(self):
        return [TEST_DATASET]

    async def extract(self, dataset_id, geometry, time_range=None):
        raise NotImplementedError


class UnwiredSTACConnector(STACMixin, BaseConnector):
    slug = "test_stac_unwired"
    display_name = "Unwired STAC"
    base_url = "https://stac.test/api"
    protocol = "stac_cog"

    supports_raster = True  # declared, but no collections wired

    async def list_datasets(self):
        return []

    async def extract(self, dataset_id, geometry, time_range=None):
        raise NotImplementedError


class FakeWCSConnector(WCSMixin, BaseConnector):
    slug = "test_wcs"
    display_name = "Test WCS"
    base_url = "https://wcs.test/wcs"
    protocol = "wcs"

    supports_raster = True

    def _raster_coverage_id(self, dataset_id: str) -> str | None:
        return "test_coverage" if dataset_id == "test_wcs:elevation" else None

    async def list_datasets(self):
        return []

    async def extract(self, dataset_id, geometry, time_range=None):
        raise NotImplementedError


def _mock_registry(connector_cls):
    """Patch the engine's discover/get_connector to serve a test connector."""
    return (
        patch("cas.extract.engine.discover"),
        patch("cas.extract.engine.get_connector", return_value=connector_cls),
    )


# ── Request-model validation ────────────────────────────────────────


class TestRasterRequestValidation:
    def test_default_output_is_stats(self, sample_geometry):
        req = AttributeRequest(geometry=sample_geometry, dataset_ids=["p:a"])
        assert req.output is OutputMode.STATS
        assert req.resampling is RasterResampling.NEAREST

    def test_stats_requires_geometry(self):
        with pytest.raises(ValidationError, match="requires 'geometry'"):
            AttributeRequest(dataset_ids=["p:a"])

    def test_stats_rejects_bbox(self, sample_geometry):
        with pytest.raises(ValidationError, match="raster-only"):
            AttributeRequest(
                geometry=sample_geometry,
                bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
                dataset_ids=["p:a"],
            )

    def test_stats_rejects_target_resolution(self, sample_geometry):
        with pytest.raises(ValidationError, match="target_resolution"):
            AttributeRequest(
                geometry=sample_geometry, dataset_ids=["p:a"], target_resolution=0.01,
            )

    def test_raster_requires_bbox(self):
        with pytest.raises(ValidationError, match="requires 'bbox'"):
            AttributeRequest(output="raster", dataset_ids=["p:a"])

    def test_raster_rejects_geometry(self, sample_geometry):
        with pytest.raises(ValidationError, match="bbox-mode only"):
            AttributeRequest(
                output="raster",
                geometry=sample_geometry,
                bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
                dataset_ids=["p:a"],
            )

    def test_raster_takes_exactly_one_dataset(self):
        with pytest.raises(ValidationError, match="exactly one dataset_id"):
            AttributeRequest(
                output="raster",
                bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
                dataset_ids=["p:a", "p:b"],
            )

    def test_raster_rejects_degenerate_bbox(self):
        with pytest.raises(ValidationError, match="min_lon < max_lon"):
            AttributeRequest(
                output="raster",
                bbox=BoundingBox(min_lon=1, min_lat=0, max_lon=0, max_lat=1),
                dataset_ids=["p:a"],
            )

    def test_raster_rejects_non_default_target_crs(self):
        # v1 limitation: native-CRS passthrough only — reprojection unsupported.
        with pytest.raises(ValidationError, match="native-CRS passthrough"):
            AttributeRequest(
                output="raster",
                bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
                dataset_ids=["p:a"],
                target_crs="EPSG:32633",
            )

    def test_valid_raster_request(self):
        req = AttributeRequest(
            output="raster",
            bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
            dataset_ids=["p:a"],
            target_resolution=0.01,
            resampling="bilinear",
        )
        assert req.output is OutputMode.RASTER
        assert req.resampling is RasterResampling.BILINEAR

    def test_invalid_resampling_rejected(self):
        with pytest.raises(ValidationError):
            AttributeRequest(
                output="raster",
                bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
                dataset_ids=["p:a"],
                resampling="lanczos_supersampled",
            )


# ── STAC mosaic correctness ─────────────────────────────────────────


class TestSTACMosaic:
    @respx.mock
    async def test_two_tile_mosaic_values_seam_and_transform(self, two_tiles, tmp_path):
        west, east = two_tiles
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(west, east))
        )
        out = tmp_path / "mosaic.tif"

        async with TwoTileSTACConnector() as conn:
            result = await conn.extract_raster(
                dataset_id="test_stac:elevation", bbox=BBOX, output_path=out,
            )

        assert isinstance(result, RasterResult)
        assert result.path == out
        assert out.exists()
        assert result.nodata == NODATA
        assert "2 items" in result.provenance

        with rasterio.open(out) as src:
            assert str(src.crs) == "EPSG:4326"
            assert src.nodata == NODATA
            # Native resolution preserved, bbox-clipped extent.
            assert src.transform.a == pytest.approx(RES)
            assert src.transform.e == pytest.approx(-RES)
            assert src.bounds.left == pytest.approx(BBOX[0])
            assert src.bounds.top == pytest.approx(BBOX[3])
            assert src.width == 20 and src.height == 12
            arr = src.read(1)
            # Transform continuity across the seam: one grid, no offset.
            row, col_w = src.index(10.7, 50.5)
            _, col_e = src.index(11.3, 50.5)
            assert arr[row, col_w] == pytest.approx(100.0)
            assert arr[row, col_e] == pytest.approx(200.0)
            # The west tile's seam-adjacent nodata stripe survives the merge.
            _, col_seam = src.index(10.975, 50.5)
            assert arr[row, col_seam] == pytest.approx(NODATA)

        assert result.transform == pytest.approx(
            (RES, 0.0, BBOX[0], 0.0, -RES, BBOX[3])
        )
        assert result.shape == (12, 20)

    @respx.mock
    async def test_target_resolution_and_resampling(self, two_tiles, tmp_path):
        west, east = two_tiles
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(west, east))
        )
        out = tmp_path / "coarse.tif"

        async with TwoTileSTACConnector() as conn:
            await conn.extract_raster(
                dataset_id="test_stac:elevation",
                bbox=BBOX,
                output_path=out,
                target_resolution=0.1,
                resampling=RasterResampling.AVERAGE,
            )

        with rasterio.open(out) as src:
            assert src.transform.a == pytest.approx(0.1)
            assert src.width == 10 and src.height == 6

    @respx.mock
    async def test_half_pixel_offset_grid_leaves_no_uncovered_edge(self, tmp_path):
        """Copernicus-style grids (pixel centers on degree lines) must not
        yield a silently empty east column when the bbox isn't grid-aligned
        (regression: rasterio.merge rounding left the last column 0-filled).
        """
        from rasterio.transform import from_origin

        tiles = []
        for name, west, value in [("w", 10.0, 100.0), ("e", 11.0, 200.0)]:
            path = tmp_path / f"offset_{name}.tif"
            width = height = round(1 / RES)
            transform = from_origin(west - RES / 2, 51.0 + RES / 2, RES, RES)
            arr = np.full((height, width), value, dtype=np.float32)
            with rasterio.open(
                path, "w", driver="GTiff", height=height, width=width, count=1,
                dtype="float32", crs="EPSG:4326", transform=transform, nodata=NODATA,
            ) as dst:
                dst.write(arr, 1)
            tiles.append(path)
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(*tiles))
        )

        # bbox deliberately not aligned to the half-pixel-offset source grid
        out = tmp_path / "offset_mosaic.tif"
        async with TwoTileSTACConnector() as conn:
            await conn.extract_raster(
                dataset_id="test_stac:elevation",
                bbox=(10.47, 50.22, 11.53, 50.81),
                output_path=out,
            )

        with rasterio.open(out) as src:
            arr = src.read(1)
            # Every pixel is covered by a source tile: no nodata, no 0-fill.
            assert not (arr == NODATA).any()
            assert not (arr == 0.0).any()
            assert set(np.unique(arr)) == {100.0, 200.0}
            # Output grid aligns with the source grid, expanded <= 1 px/edge.
            assert src.bounds.left == pytest.approx(10.47 - RES / 2, abs=RES)
            assert src.bounds.left <= 10.47 and src.bounds.right >= 11.53
            assert src.bounds.bottom <= 50.22 and src.bounds.top >= 50.81

    @respx.mock
    async def test_no_items_raises(self, tmp_path):
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json={"features": []})
        )
        async with TwoTileSTACConnector() as conn:
            with pytest.raises(ProtocolError, match="No STAC items"):
                await conn.extract_raster(
                    dataset_id="test_stac:elevation",
                    bbox=BBOX,
                    output_path=tmp_path / "x.tif",
                )

    async def test_unwired_stac_connector_raises_capability_error(self, tmp_path):
        async with UnwiredSTACConnector() as conn:
            with pytest.raises(RasterUnsupportedError, match="not wired"):
                await conn.extract_raster(
                    dataset_id="test_stac_unwired:elevation",
                    bbox=BBOX,
                    output_path=tmp_path / "x.tif",
                )


# ── WCS passthrough ─────────────────────────────────────────────────


def _geotiff_bytes() -> bytes:
    from rasterio.io import MemoryFile

    arr = np.full((8, 10), 42.0, dtype=np.float32)
    transform = from_bounds(10.5, 50.2, 11.5, 51.0, 10, 8)
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=8, width=10, count=1, dtype="float32",
            crs="EPSG:4326", transform=transform, nodata=NODATA,
        ) as dst:
            dst.write(arr, 1)
        return memfile.read()


class TestWCSRasterPassthrough:
    @respx.mock
    async def test_passthrough_writes_payload_verbatim(self, tmp_path):
        payload = _geotiff_bytes()
        route = respx.get("https://wcs.test/wcs/").mock(
            return_value=httpx.Response(200, content=payload)
        )
        out = tmp_path / "wcs.tif"

        async with FakeWCSConnector() as conn:
            result = await conn.extract_raster(
                dataset_id="test_wcs:elevation", bbox=BBOX, output_path=out,
            )

        assert route.called
        assert out.read_bytes() == payload  # byte-for-byte passthrough
        assert result.crs == "EPSG:4326"
        assert result.shape == (8, 10)
        assert result.nodata == NODATA
        assert "test_coverage" in result.provenance

    @respx.mock
    async def test_xml_error_payload_raises(self, tmp_path):
        respx.get("https://wcs.test/wcs/").mock(
            return_value=httpx.Response(200, content=b"<?xml version='1.0'?><err/>")
        )
        async with FakeWCSConnector() as conn:
            with pytest.raises(DataFormatError, match="WCS error"):
                await conn.extract_raster(
                    dataset_id="test_wcs:elevation",
                    bbox=BBOX,
                    output_path=tmp_path / "x.tif",
                )

    @respx.mock
    async def test_unreadable_payload_is_removed_and_raises(self, tmp_path):
        respx.get("https://wcs.test/wcs/").mock(
            return_value=httpx.Response(200, content=b"not a tiff at all")
        )
        out = tmp_path / "bad.tif"
        async with FakeWCSConnector() as conn:
            with pytest.raises(DataFormatError, match="not a readable GeoTIFF"):
                await conn.extract_raster(
                    dataset_id="test_wcs:elevation", bbox=BBOX, output_path=out,
                )
        assert not out.exists()

    async def test_target_resolution_unsupported_for_wcs(self, tmp_path):
        async with FakeWCSConnector() as conn:
            with pytest.raises(RasterUnsupportedError, match="native-resolution"):
                await conn.extract_raster(
                    dataset_id="test_wcs:elevation",
                    bbox=BBOX,
                    output_path=tmp_path / "x.tif",
                    target_resolution=0.01,
                )

    async def test_unwired_dataset_raises(self, tmp_path):
        async with FakeWCSConnector() as conn:
            with pytest.raises(RasterUnsupportedError, match="not wired"):
                await conn.extract_raster(
                    dataset_id="test_wcs:other",
                    bbox=BBOX,
                    output_path=tmp_path / "x.tif",
                )


# ── Capability gating ───────────────────────────────────────────────


class TestSupportsRasterGating:
    def test_base_connector_defaults_to_no_raster(self):
        assert BaseConnector.supports_raster is False

    def test_rest_connector_is_stats_only(self):
        from cas.connectors.isric_soilgrids import ISRICSoilGridsConnector

        assert ISRICSoilGridsConnector.supports_raster is False

    def test_wired_connectors_declare_support(self):
        from cas.connectors.copernicus_dem import CopernicusDEMConnector
        from cas.connectors.tandem_x import TanDEMXConnector
        from cas.connectors.usgs_3dep import USGS3DEPConnector

        for cls in (CopernicusDEMConnector, USGS3DEPConnector):
            assert cls.supports_raster is True
            assert cls.stac_raster_collections
            assert cls.stac_sign_assets is True
        assert TanDEMXConnector.supports_raster is True

    async def test_base_default_extract_raster_raises(self, tmp_path):
        from cas.connectors.isric_soilgrids import ISRICSoilGridsConnector

        async with ISRICSoilGridsConnector() as conn:
            with pytest.raises(RasterUnsupportedError, match="does not support raster"):
                await conn.extract_raster(
                    dataset_id="isric_soilgrids:clay_0-5cm",
                    bbox=BBOX,
                    output_path=tmp_path / "x.tif",
                )

    async def test_engine_gates_before_touching_the_connector(self, tmp_path):
        from cas.connectors.isric_soilgrids import ISRICSoilGridsConnector

        d, g = _mock_registry(ISRICSoilGridsConnector)
        with d, g, pytest.raises(RasterUnsupportedError, match="stats-only"):
            await extract_raster("isric_soilgrids:clay_0-5cm", BBOX, tmp_path)


# ── Engine raster path ──────────────────────────────────────────────


class TestEngineRasterPath:
    @respx.mock
    async def test_bypasses_result_cache_and_qc(self, two_tiles, tmp_path):
        west, east = two_tiles
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(west, east))
        )
        cache = MagicMock()
        qc = MagicMock()
        d, g = _mock_registry(TwoTileSTACConnector)
        with (
            d, g,
            patch("cas.extract.engine.get_result_cache", cache),
            patch("cas.extract.engine.validate_result", qc),
        ):
            result = await extract_raster(
                "test_stac:elevation", BBOX, tmp_path / "out",
            )

        cache.assert_not_called()
        qc.assert_not_called()
        assert result.path.exists()

    @respx.mock
    async def test_engine_enriches_license_and_variable(self, two_tiles, tmp_path):
        west, east = two_tiles
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(west, east))
        )
        d, g = _mock_registry(TwoTileSTACConnector)
        with d, g:
            result = await extract_raster("test_stac:elevation", BBOX, tmp_path)

        assert result.license == "Test License"
        assert result.variable == "elevation"
        assert result.units == "m"
        assert result.provider == "test_stac"
        assert result.path == tmp_path / "test_stac_elevation.tif"

    @respx.mock
    async def test_bbox_accepts_tuple_dict_and_model(self, two_tiles, tmp_path):
        west, east = two_tiles
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(west, east))
        )
        d, g = _mock_registry(TwoTileSTACConnector)
        boxes = [
            BBOX,
            {"min_lon": BBOX[0], "min_lat": BBOX[1], "max_lon": BBOX[2], "max_lat": BBOX[3]},
            BoundingBox(min_lon=BBOX[0], min_lat=BBOX[1], max_lon=BBOX[2], max_lat=BBOX[3]),
        ]
        with d, g:
            for i, box in enumerate(boxes):
                result = await extract_raster(
                    "test_stac:elevation", box, tmp_path, filename=f"v{i}.tif",
                )
                assert result.shape == (12, 20)

    async def test_stats_engine_rejects_raster_requests(self):
        request = AttributeRequest(
            output="raster",
            bbox=BoundingBox(min_lon=0, min_lat=0, max_lon=1, max_lat=1),
            dataset_ids=["p:a"],
        )
        with pytest.raises(ExtractionError, match="in-process"):
            await extract(request)

    async def test_unknown_provider_raises(self, tmp_path):
        with (
            patch("cas.extract.engine.discover"),
            pytest.raises(ExtractionError, match="Unknown provider"),
        ):
            await extract_raster("nope:elevation", BBOX, tmp_path)


# ── Facade round-trip ───────────────────────────────────────────────


class TestFacadeRoundTrip:
    @respx.mock
    def test_extract_raster_sync_writes_real_geotiff(self, two_tiles, tmp_path):
        west, east = two_tiles
        respx.post("https://stac.test/api/search").mock(
            return_value=httpx.Response(200, json=_stac_items(west, east))
        )
        out_dir = tmp_path / "facade_out"
        d, g = _mock_registry(TwoTileSTACConnector)
        with d, g:
            result = cas.extract_raster_sync(
                "test_stac:elevation", bbox=BBOX, output_dir=out_dir,
            )

        assert isinstance(result, cas.RasterResult)
        assert result.path.parent == out_dir
        # The discretization-grade contract: domain-bbox, tile-mosaicked,
        # native-resolution, EPSG:4326 GeoTIFF with correct nodata at a path.
        with rasterio.open(result.path) as src:
            assert str(src.crs) == "EPSG:4326"
            assert src.nodata == NODATA
            assert src.transform.a == pytest.approx(RES)
            assert src.bounds.left == pytest.approx(BBOX[0])
            assert src.bounds.right == pytest.approx(BBOX[2])
            data = src.read(1)
            assert (data == 100.0).any() and (data == 200.0).any()

    def test_facade_exports_raster_surface(self):
        for name in (
            "extract_raster", "extract_raster_sync",
            "RasterResult", "BoundingBox", "OutputMode", "RasterResampling",
        ):
            assert name in cas.__all__
            assert hasattr(cas, name)

    async def test_sync_wrapper_refuses_running_loop(self, tmp_path):
        with pytest.raises(RuntimeError, match="extract_raster_sync"):
            cas.extract_raster_sync("p:a", BBOX, tmp_path)
