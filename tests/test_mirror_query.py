# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Subset query tests: bbox row-group pruning, buffer behavior, column
projection, GeoPackage output schema, license passthrough, and the lazy
materialization trigger. Hermetic via respx + tiny synthetic shapefiles."""

from __future__ import annotations

import httpx
import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

import cas
from cas.core.exceptions import MirrorError
from cas.mirror import mirror_subset_sync

from .mirror_utils import FAKE_URL, build_fake_zip, make_fake_dataset, registered


@pytest.fixture
def mirror_root(tmp_path):
    from cas.core.config import _overrides, get_settings

    saved = dict(_overrides)
    cas.configure(mirror_dir=tmp_path / "mirror")
    yield tmp_path / "mirror"
    _overrides.clear()
    _overrides.update(saved)
    get_settings.cache_clear()


def _read(path):
    import geopandas as gpd

    return gpd.read_file(path)


class TestBboxSubset:
    @respx.mock
    def test_no_buffer_excludes_buffer_only_feature(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            # bbox (2,2,4,4) with buffer 0 catches 'inside'? No — 'inside' is
            # box(0,0,1,1), outside (2,2,4,4). 'straddle' box(3.9..4.5) crosses
            # the edge → included. 'buffer_only' box(4.2..5) is out. 'far' out.
            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0),
                output_dir=tmp_path / "out", buffer_deg=0.0,
            )
            gdf = _read(result.path)
            assert set(gdf["name"]) == {"straddle"}
            assert result.feature_count == 1
            assert result.buffer_deg == 0.0

    @respx.mock
    def test_buffer_includes_straddling_neighbor(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset(default_buffer_deg=0.5)):
            # Default buffer 0.5 grows (2,2,4,4) to (1.5,1.5,4.5,4.5):
            # 'straddle' in, 'buffer_only' box(4.2..5) now intersects, 'inside'
            # box(0,0,1,1) still out, 'far' out.
            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            gdf = _read(result.path)
            assert set(gdf["name"]) == {"straddle", "buffer_only"}
            assert result.buffer_deg == 0.5

    @respx.mock
    def test_box_covering_inside_feature(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = mirror_subset_sync(
                "fakeveg", bbox=(-0.5, -0.5, 2.0, 2.0),
                output_dir=tmp_path / "out", buffer_deg=0.0,
            )
            assert set(_read(result.path)["name"]) == {"inside"}

    @respx.mock
    def test_empty_result_is_valid(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = mirror_subset_sync(
                "fakeveg", bbox=(20.0, 20.0, 21.0, 21.0),
                output_dir=tmp_path / "out", buffer_deg=0.0,
            )
            assert result.feature_count == 0
            assert result.path.is_file()  # schema-complete empty gpkg


class TestColumnsAndSchema:
    @respx.mock
    def test_default_keeps_all_columns(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            assert {"fid_int", "name", "value"} <= set(result.columns)
            gdf = _read(result.path)
            assert {"fid_int", "name", "value"} <= set(gdf.columns)

    @respx.mock
    def test_column_projection(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0),
                output_dir=tmp_path / "out", columns=["value"],
            )
            gdf = _read(result.path)
            assert "value" in gdf.columns
            assert "name" not in gdf.columns
            assert gdf.geometry is not None  # geometry always rides along

    @respx.mock
    def test_unknown_column_rejected(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()), pytest.raises(MirrorError, match="Unknown column"):
            mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0),
                output_dir=tmp_path / "out", columns=["nope"],
            )

    @respx.mock
    def test_output_is_single_layer_geopackage(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()) as ds:
            import pyogrio

            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            assert result.path.suffix == ".gpkg"
            layers = pyogrio.list_layers(result.path)
            assert [name for name, _ in layers] == [ds.slug]


class TestLicensePassthrough:
    @respx.mock
    def test_result_carries_license_and_provenance(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            assert result.license == "CC-BY-4.0"
            assert result.attribution == "Fake Data Consortium 2026"
            assert result.citation.startswith("Fake et al.")
            assert "curated mirror fakeveg==1.0" in result.provenance
            assert "GeoParquet row-group bbox subset" in result.provenance
            assert result.dataset_id == "fakeveg==1.0"

    @respx.mock
    def test_wokam_style_unconfirmed_flag_passthrough(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        ds = make_fake_dataset(slug="flagveg")
        ds.license.license_flags.append("unconfirmed")
        with registered(ds):
            result = mirror_subset_sync(
                "flagveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            assert "unconfirmed" in result.license_flags


class TestLazyMaterialization:
    @respx.mock
    def test_first_query_triggers_materialize(self, mirror_root, tmp_path):
        route = respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()) as ds:
            from cas.mirror import is_materialized

            assert not is_materialized(ds)
            mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            assert is_materialized(ds)
            assert route.call_count == 1

    @respx.mock
    def test_offline_mode_blocks_query(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        cas.configure(mirror_offline=True)
        from cas.core.exceptions import MirrorOfflineError

        with registered(make_fake_dataset()), pytest.raises(MirrorOfflineError, match="cas mirror sync"):
            mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )


class TestReprojectedFilter:
    @respx.mock
    def test_non_4326_layer_filtered_correctly(self, mirror_root, tmp_path):
        # Source layer in Web Mercator; the EPSG:4326 query bbox is reprojected
        # for the filter (design §3, as the native GLHYMPS handler does).
        zip_bytes = build_fake_zip(tmp_path, crs="EPSG:3857", shp_name="data")
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=zip_bytes))
        with registered(make_fake_dataset()):
            result = mirror_subset_sync(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0),
                output_dir=tmp_path / "out", buffer_deg=0.5,
            )
            names = set(_read(result.path)["name"])
            assert "straddle" in names
            assert "far" not in names
            assert "EPSG:3857" in result.crs or "3857" in result.crs


class TestAsyncFacade:
    @respx.mock
    async def test_async_subset(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = await cas.mirror_subset(
                "fakeveg", bbox=(2.0, 2.0, 4.0, 4.0), output_dir=tmp_path / "out",
            )
            assert result.feature_count >= 1


class TestBboxNormalization:
    def test_invalid_bbox_rejected(self):
        from cas.mirror.query import _normalize_bbox

        with pytest.raises(MirrorError):
            _normalize_bbox((4.0, 2.0, 2.0, 4.0))

    def test_tuple_and_dict_forms(self):
        from cas.mirror.query import _normalize_bbox

        assert _normalize_bbox((1.0, 2.0, 3.0, 4.0)) == (1.0, 2.0, 3.0, 4.0)
        assert _normalize_bbox(
            {"min_lon": 1.0, "min_lat": 2.0, "max_lon": 3.0, "max_lat": 4.0}
        ) == (1.0, 2.0, 3.0, 4.0)
