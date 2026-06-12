# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Per-unit materialization + RGI-style subset tests (hermetic).

Covers the slice-2a contract: multi-unit lazy materialization, the
Earthdata-authenticated download path through the store, bbox→region
selection driving which units land on disk, manifests accumulating units,
and credentials never reaching the manifest.
"""

from __future__ import annotations

import httpx
import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

from cas.core.exceptions import MirrorUnitError
from cas.mirror import (
    dataset_dir,
    ensure_materialized,
    ensure_units_materialized,
    get_mirror_dataset,
    is_unit_materialized,
    load_manifest,
    register_unit_resolver,
    unit_paths,
    verify,
)
from cas.mirror.query import mirror_subset_sync
from cas.mirror.units import _UNIT_RESOLVERS

from .mirror_utils import build_fake_zip, make_fake_dataset, make_fake_unit_dataset, registered

UNIT_URL = "https://mirror.invalid/fakeunits_{unit}.zip"


@pytest.fixture
def unit_resolver():
    """bbox→unit resolver for the fake dataset: unit 'a' covers lon<5,
    unit 'b' covers lon>=5 (features in mirror_utils span both)."""
    table = {"a": (-1.0, -1.0, 5.0, 6.0), "b": (5.0, 5.0, 10.0, 10.0)}

    def resolve(bbox, settings):
        _ = settings
        return sorted(
            u for u, e in table.items()
            if bbox[0] <= e[2] and bbox[2] >= e[0] and bbox[1] <= e[3] and bbox[3] >= e[1]
        )

    register_unit_resolver("fakeunits", resolve)
    yield
    _UNIT_RESOLVERS.pop("fakeunits", None)


def _mock_unit_zips(tmp_path, units=("a", "b")):
    for unit in units:
        payload = build_fake_zip(tmp_path, shp_name=f"data_{unit}")
        respx.get(UNIT_URL.format(unit=unit)).mock(
            return_value=httpx.Response(200, content=payload)
        )


class TestUnitMaterialization:
    @respx.mock
    def test_single_unit_materializes_only_that_unit(self, mirror_root, tmp_path):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()) as ds:
            paths = ensure_units_materialized(ds, ["a"])
            assert set(paths) == {"a"}
            assert paths["a"]["data"].is_file()
            assert paths["a"]["data"].suffix == ".parquet"
            assert is_unit_materialized(ds, "a")
            assert not is_unit_materialized(ds, "b")

            manifest = load_manifest(ds)
            assert manifest.units == ["a"]
            assert all(f.unit == "a" for f in manifest.files)
            # The {access_date} placeholder is rendered at materialization.
            assert "{access_date}" not in manifest.citation
            assert "Accessed 20" in manifest.citation

    @respx.mock
    def test_second_unit_accumulates_into_manifest(self, mirror_root, tmp_path):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()) as ds:
            ensure_units_materialized(ds, ["a"])
            first_count = load_manifest(ds).feature_count
            ensure_units_materialized(ds, ["b"])
            manifest = load_manifest(ds)
            assert manifest.units == ["a", "b"]
            assert manifest.feature_count == 2 * first_count
            assert verify(ds) == []
            # Unit subdirs per design §2 layout.
            assert (dataset_dir(ds) / "units" / "a").is_dir()
            assert (dataset_dir(ds) / "units" / "b").is_dir()

    @respx.mock
    def test_materialized_units_are_local_only(self, mirror_root, tmp_path):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()) as ds:
            ensure_units_materialized(ds, ["a", "b"])
            calls_after_sync = len(respx.calls)
            ensure_units_materialized(ds, ["a", "b"])
            assert len(respx.calls) == calls_after_sync

    @respx.mock
    def test_archives_are_dropped_after_conversion(self, mirror_root, tmp_path):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()) as ds:
            ensure_units_materialized(ds, ["a"])
            ddir = dataset_dir(ds)
            assert not list(ddir.glob("*.zip"))
            assert not list(ddir.glob("*.part"))
            assert not (ddir / "_extract").exists()

    @respx.mock
    def test_global_path_refuses_unit_dataset_and_vice_versa(self, mirror_root, tmp_path):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()) as ds, \
                pytest.raises(MirrorUnitError, match="unit-structured"):
            ensure_materialized(ds)
        with registered(make_fake_dataset()) as ds, \
                pytest.raises(MirrorUnitError, match="not unit-structured"):
            ensure_units_materialized(ds, ["a"])

    @respx.mock
    def test_earthdata_auth_used_and_token_never_in_manifest(
        self, mirror_root, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("EARTHDATA_TOKEN", "SUPER-SECRET-TOKEN")
        seen_auth = []

        def serve(request):
            seen_auth.append(request.headers.get("authorization"))
            return httpx.Response(200, content=build_fake_zip(tmp_path, shp_name="data_a"))

        respx.get(UNIT_URL.format(unit="a")).mock(side_effect=serve)
        ds = make_fake_unit_dataset(units=("a",), auth="earthdata")
        with registered(ds):
            ensure_units_materialized(ds, ["a"])
            assert seen_auth == ["Bearer SUPER-SECRET-TOKEN"]
            manifest_text = (dataset_dir(ds) / "manifest.json").read_text()
            assert "SUPER-SECRET-TOKEN" not in manifest_text


class TestUnitSubset:
    @respx.mock
    def test_bbox_materializes_only_intersecting_units(
        self, mirror_root, tmp_path, unit_resolver
    ):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()) as ds:
            # bbox only touches unit 'a' (lon < 5).
            result = mirror_subset_sync(
                "fakeunits", (0.0, 0.0, 2.0, 2.0), tmp_path / "out"
            )
            assert is_unit_materialized(ds, "a")
            assert not is_unit_materialized(ds, "b")
            assert result.feature_count == 1  # the "inside" box(0,0,1,1)
            assert result.path.is_file()
            assert "units a" in result.provenance

    @respx.mock
    def test_bbox_across_units_concatenates(self, mirror_root, tmp_path, unit_resolver):
        _mock_unit_zips(tmp_path)
        with registered(make_fake_unit_dataset()):
            result = mirror_subset_sync(
                "fakeunits", (0.0, 0.0, 9.5, 9.5), tmp_path / "out"
            )
            # Both unit copies of the 4 synthetic features intersect.
            assert result.feature_count == 8
            assert "units a,b" in result.provenance

    @respx.mock
    def test_bbox_touching_no_unit_returns_empty_result(
        self, mirror_root, tmp_path, unit_resolver
    ):
        with registered(make_fake_unit_dataset()) as ds:
            result = mirror_subset_sync(
                "fakeunits", (50.0, 50.0, 51.0, 51.0), tmp_path / "out"
            )
            assert result.feature_count == 0
            assert result.path.is_file()
            assert not is_unit_materialized(ds, "a")
            assert not is_unit_materialized(ds, "b")
            import geopandas as gpd

            assert len(gpd.read_file(result.path)) == 0

    def test_rgi7_subset_requires_units_resolver(self, mirror_root, tmp_path):
        # The shipped rgi7 entry has a registered resolver — an open-ocean
        # bbox produces the honest empty result without any download.
        result = mirror_subset_sync("rgi7", (-40.0, 28.0, -38.0, 30.0), tmp_path / "out")
        assert result.feature_count == 0
        assert result.license == "CC-BY-4.0"


class TestRGIEndToEnd:
    @respx.mock
    def test_iceland_bbox_pulls_region_06_through_urs(
        self, mirror_root, tmp_path, monkeypatch
    ):
        """The real rgi7 registry entry, exercised through the mocked URS
        dance: bbox over Iceland → region 06 only → authenticated download
        → GeoParquet unit → manifest."""
        monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
        ds = get_mirror_dataset("rgi7")
        (iceland,) = ds.sources_for_unit("06")
        urs = "https://urs.earthdata.nasa.gov/oauth/authorize?cid=1"
        back = iceland.url + "?code=ok"
        payload = build_fake_zip(tmp_path, shp_name="data_06")

        respx.get(iceland.url).mock(
            side_effect=[
                httpx.Response(302, headers={"location": urs}),
                httpx.Response(200, content=payload),
            ]
        )
        respx.get(urs).mock(return_value=httpx.Response(302, headers={"location": back}))
        respx.get(back).mock(return_value=httpx.Response(302, headers={"location": iceland.url}))

        result = mirror_subset_sync("rgi7", (-20.0, 64.0, -18.0, 65.0), tmp_path / "out")
        assert load_manifest(ds).units == ["06"]
        assert "Date Accessed" in load_manifest(ds).citation
        assert result.feature_count == 0  # synthetic features are off-Iceland
        paths = unit_paths(ds, "06")
        assert paths["outlines"].suffix == ".parquet"
        # Named after the shapefile inside the archive (for the real RGI
        # distribution that is RGI2000-v7.0-G-06_iceland.shp).
        assert paths["outlines"].name == "data_06.parquet"
        assert paths["outlines"].parent == dataset_dir(ds) / "units" / "06"
